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
pytestmark = pytest.mark.skipif(not IMAGE, reason="Set KEA_EXPORTER_TEST_IMAGE to test a built Docker image")
ROOT = Path(__file__).resolve().parent.parent


def docker(*arguments):
    return subprocess.run(["docker", *arguments], check=True, capture_output=True, text=True, timeout=30).stdout


def test_image_installs_only_locked_runtime_packages():
    report = json.loads(
        docker(
            "run",
            "--rm",
            "--entrypoint",
            "python",
            IMAGE,
            "-c",
            """
import importlib.metadata
import json
import os
import shutil

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
    'license': project.read_text('licenses/LICENSE'),
}))
""",
        )
    )
    assert report["uid"] != 0
    assert not report["editable"]
    assert not report["writable"]
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
