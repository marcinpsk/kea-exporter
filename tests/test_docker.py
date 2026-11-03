"""Run the built image against the real CLI and a local Kea control server."""

import json
import os
import socket
import subprocess
import time
import tomllib
from pathlib import Path
from uuid import uuid4

import pytest
import requests
from prometheus_client.parser import text_string_to_metric_families

IMAGE = os.environ.get("KEA_EXPORTER_TEST_IMAGE")
PLATFORM = os.environ.get("KEA_EXPORTER_TEST_PLATFORM")
pytestmark = pytest.mark.skipif(not IMAGE, reason="Set KEA_EXPORTER_TEST_IMAGE to test a built Docker image")
ROOT = Path(__file__).resolve().parent.parent


def docker(*arguments):
    if arguments[0] in {"run", "build"} and PLATFORM:
        arguments = (arguments[0], "--platform", PLATFORM, *arguments[1:])
    return subprocess.run(["docker", *arguments], check=True, capture_output=True, text=True, timeout=30).stdout


def check_image_installation(image):
    """Inspect recorded package paths and ancestors as the container's normal user."""
    report = json.loads(
        docker(
            "run",
            "--rm",
            "--entrypoint",
            "python",
            image,
            "-c",
            """
import importlib.metadata
import json
import os
import shutil

installed_paths = set()
for distribution in importlib.metadata.distributions():
    for file in distribution.files or []:
        path = file.locate().resolve()
        installed_paths.add(path)
        installed_paths.update(path.parents)

project = importlib.metadata.distribution('kea-exporter')
print(json.dumps({
    'uid': os.getuid(),
    'editable': json.loads(project.read_text('direct_url.json') or '{}').get('dir_info', {}).get('editable', False),
    'versions': {
        dist.metadata['Name'].lower().replace('_', '-'): dist.version
        for dist in importlib.metadata.distributions()
    },
    'uv': shutil.which('uv'),
    'uvx': shutil.which('uvx'),
    'writable': os.access(project.locate_file(''), os.W_OK),
    'writable_paths': [str(path) for path in sorted(installed_paths) if os.access(path, os.W_OK)],
    'license': project.read_text('licenses/LICENSE'),
}))
""",
        )
    )
    assert report["uid"] != 0
    assert not report["editable"]
    assert not report["writable"]
    assert not report["writable_paths"], f"writable package paths: {report['writable_paths']}"
    assert report["uv"] is None
    assert report["uvx"] is None
    assert report["license"] == (ROOT / "LICENSE").read_text()

    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    packages = {package["name"]: package for package in lock["package"]}
    expected = {}
    pending = ["kea-exporter"]
    while pending:
        name = pending.pop()
        if name in expected:
            continue
        package = packages[name]
        expected[name] = package["version"]
        pending.extend(dependency["name"] for dependency in package.get("dependencies", []))
    assert report["versions"] == expected


def test_image_installs_only_locked_runtime_packages():
    check_image_installation(IMAGE)


@pytest.fixture
def permission_images(tmp_path):
    """Build derivative images and remove only this test's image references."""
    paths = {
        "module": "Path(kea_exporter.__file__)",
        "package": "Path(kea_exporter.__file__).parent",
        "ancestor": "Path(sys.prefix) / 'lib'",
    }
    images = []

    def build(target, mode):
        image = f"kea-exporter-permissions-{uuid4().hex}:test"
        directory = tmp_path / f"image-{len(images)}"
        directory.mkdir()
        (directory / "Dockerfile").write_text(
            f"FROM {IMAGE}\n"
            "USER root\n"
            'RUN python -c "import os; import sys; from pathlib import Path; import kea_exporter; '
            f'os.chmod({paths[target]}, {mode})"\n'
            "USER kea-exporter:kea-exporter\n"
            f"LABEL test-image={image}\n"
        )
        docker("build", "--tag", image, str(directory))
        images.append(image)
        return image

    try:
        yield build
    finally:
        for image in reversed(images):
            docker("image", "rm", image)


def test_derivative_image_ids_are_isolated(permission_images):
    first = permission_images("module", 0o666)
    second = permission_images("module", 0o666)
    ids = docker("image", "inspect", "--format", "{{.Id}}", first, second).splitlines()
    assert ids[0] != ids[1], "One test must not delete an image ID that another test is running"


@pytest.mark.parametrize("target,mode", [("module", 0o666), ("package", 0o777), ("ancestor", 0o777)])
def test_image_check_rejects_writable_package_paths(permission_images, target, mode):
    image = permission_images(target, mode)
    with pytest.raises(AssertionError, match="writable package paths"):
        check_image_installation(image)


def test_image_entrypoint_exposes_cli_help():
    output = docker("run", "--rm", "--read-only", IMAGE, "--help")
    assert "Usage: kea-exporter [OPTIONS] TARGETS..." in output
    assert "--ca-bundle" in output


def test_image_serves_kea_metrics_with_a_read_only_filesystem(http_server):
    kea = http_server()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    name = f"kea-exporter-test-{uuid4().hex}"
    docker(
        "run",
        "--detach",
        "--name",
        name,
        "--read-only",
        "--network",
        "host",
        IMAGE,
        "--address",
        "127.0.0.1",
        "--port",
        str(port),
        kea.target,
    )
    try:
        with requests.Session() as session:
            session.trust_env = False
            deadline = time.monotonic() + 15
            while True:
                try:
                    response = session.get(f"http://127.0.0.1:{port}/metrics", timeout=1)
                    response.raise_for_status()
                    break
                except requests.ConnectionError:
                    if time.monotonic() >= deadline:
                        pytest.fail(docker("logs", name))
                    time.sleep(0.1)
        samples = [sample for family in text_string_to_metric_families(response.text) for sample in family.samples]
        assert any(
            sample.name == "kea_dhcp4_packets_sent_total"
            and sample.labels == {"server": kea.target, "operation": "ack"}
            and sample.value == 7
            for sample in samples
        )
    finally:
        docker("rm", "--force", name)
