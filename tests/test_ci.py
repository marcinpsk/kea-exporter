"""Exercise the lint tools against workflows and dependency files."""

import ast
import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("dockerfile", ["Dockerfile.alpine", "Dockerfile.debian"])
def test_release_uv_matches_the_docker_lockfile_reader(dockerfile):
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    image_versions = re.findall(r"^FROM ghcr\.io/astral-sh/uv:([\d.]+) AS uv$", (ROOT / dockerfile).read_text(), re.M)
    assert len(image_versions) == 1
    command = shlex.split(project["tool"]["semantic_release"]["build_command"])
    assert f"uv=={image_versions[0]}" in command, "Pin the release lockfile writer to the Docker uv version"


@pytest.mark.parametrize("dockerfile", ["Dockerfile.alpine", "Dockerfile.debian"])
def test_docker_python_matches_project_support_and_smoke_tests(dockerfile):
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    versions = re.findall(r"^FROM python:([\d.]+)-\S+ AS base$", (ROOT / dockerfile).read_text(), re.M)
    assert len(versions) == 1
    assert versions[0] in SpecifierSet(project["project"]["requires-python"])
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    setup_uv = next(
        step
        for step in workflow["jobs"]["build-and-push-image"]["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert setup_uv["with"]["python-version"] == versions[0]


def check_native_image_workflow(workflow, arch, runner):
    job = workflow["jobs"]["build-and-push-image"]
    matrix = job["strategy"]["matrix"]
    assert matrix["arch"] == ["amd64", "arm64"]
    assert matrix["distro"] == ["alpine", "debian"]
    assert {entry["arch"]: entry["runner"] for entry in matrix["include"]}[arch] == runner
    assert job["runs-on"] == "${{ matrix.runner }}"
    steps = job["steps"]
    assert not any(step.get("uses", "").startswith("docker/setup-qemu-action@") for step in steps)
    builds = [
        (index, step["with"])
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]
    assert len(builds) == 1, "Build each native image once, and load it for runtime smoke tests"
    build_index, build = builds[0]
    assert build["platforms"] == "linux/${{ matrix.arch }}"
    assert build["load"] == "${{ inputs.tag == '' }}"
    assert "${{ matrix.arch }}" in build["cache-to"]
    test_index = next(index for index, step in enumerate(steps) if "KEA_EXPORTER_TEST_IMAGE" in step.get("env", {}))
    assert steps[test_index]["run"].strip() == "uv run --frozen -- pytest -q tests/test_docker.py"
    assert "if" not in steps[test_index]
    assert "continue-on-error" not in steps[test_index]
    assert steps[test_index]["env"]["KEA_EXPORTER_TEST_PLATFORM"] == build["platforms"]
    digest_index = next(index for index, step in enumerate(steps) if step.get("id") == "digest")
    assert steps[digest_index]["env"]["DIGEST"] == "${{ steps.build.outputs.digest }}"
    assert steps[digest_index]["env"]["ARCH"] == "${{ matrix.arch }}"
    upload_index = next(
        index for index, step in enumerate(steps) if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert build_index < test_index < digest_index < upload_index


@pytest.mark.parametrize("arch, runner", [("amd64", "ubuntu-24.04"), ("arm64", "ubuntu-24.04-arm")])
def test_each_image_platform_is_built_and_smoke_tested_on_a_native_runner(arch, runner):
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    check_native_image_workflow(workflow, arch, runner)


def check_release_manifest_workflow(workflow):
    job = workflow["jobs"]["publish-manifests"]
    assert job["needs"] == "build-and-push-image"
    assert job["if"] == "${{ inputs.tag != '' }}"
    assert job["strategy"]["matrix"]["distro"] == ["alpine", "debian"]
    download = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"]["pattern"] == "digests-${{ matrix.distro }}-*"
    assert download["with"]["merge-multiple"] is True
    publish = next(step for step in job["steps"] if step.get("name") == "Publish the multi-platform image")
    assert "if" not in publish
    assert "continue-on-error" not in publish
    assert ["docker", "buildx", "imagetools", "create", "${arguments[@]}"] in [
        shlex.split(line) for line in publish["run"].splitlines()
    ]
    metadata = next(step["with"] for step in job["steps"] if step.get("id") == "meta")
    rules = [dict(field.split("=", 1) for field in row) for row in csv.reader(metadata["tags"].splitlines())]
    semver = [rule for rule in rules if rule["type"] == "semver"]
    assert {rule["pattern"] for rule in semver} == {
        "{{version}}-${{ matrix.distro }}",
        "{{major}}.{{minor}}-${{ matrix.distro }}",
    }
    assert all(rule["value"] == "${{ inputs.tag }}" and rule["enable"] == "${{ inputs.tag != '' }}" for rule in semver)
    latest = {rule["value"]: rule["enable"] for rule in rules if rule["type"] == "raw"}
    assert latest == {
        "latest-${{ matrix.distro }}": "${{ inputs.tag != '' }}",
        "latest": "${{ inputs.tag != '' && matrix.distro == 'debian' }}",
    }


def test_release_manifests_require_all_native_image_checks():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    check_release_manifest_workflow(workflow)


@pytest.mark.parametrize(
    "field",
    ["smoke_command", "digest_source", "architecture_source", "distro_matrix", "smoke_condition", "smoke_continue"],
)
def test_native_image_guard_rejects_invalid_workflow_commands_and_bindings(field):
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    steps = workflow["jobs"]["build-and-push-image"]["steps"]
    if field == "distro_matrix":
        workflow["jobs"]["build-and-push-image"]["strategy"]["matrix"]["distro"] = ["debian"]
    elif field in {"smoke_command", "smoke_condition", "smoke_continue"}:
        step = next(step for step in steps if "KEA_EXPORTER_TEST_IMAGE" in step.get("env", {}))
        if field == "smoke_command":
            step["run"] = "echo tests/test_docker.py"
        elif field == "smoke_condition":
            step["if"] = False
        else:
            step["continue-on-error"] = True
    else:
        step = next(step for step in steps if step.get("id") == "digest")
        step["env"]["DIGEST" if field == "digest_source" else "ARCH"] = "${{ github.sha }}"
    with pytest.raises(AssertionError):
        check_native_image_workflow(workflow, "amd64", "ubuntu-24.04")


@pytest.mark.parametrize(
    "field",
    [
        "semver_tags",
        "distro_suffix",
        "latest_distro",
        "publish_condition",
        "publish_continue",
        "publish_command",
        "echoed_publish_command",
    ],
)
def test_manifest_guard_rejects_incompatible_release_tag_rules(field):
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    steps = workflow["jobs"]["publish-manifests"]["steps"]
    metadata = next(step["with"] for step in steps if step.get("id") == "meta")
    if field == "semver_tags":
        metadata["tags"] = "\n".join(line for line in metadata["tags"].splitlines() if "type=semver" not in line)
    elif field == "distro_suffix":
        metadata["tags"] = metadata["tags"].replace("-${{ matrix.distro }}", "")
    elif field == "latest_distro":
        metadata["tags"] = metadata["tags"].replace("matrix.distro == 'debian'", "matrix.distro == 'alpine'")
    else:
        publish = next(step for step in steps if step.get("name") == "Publish the multi-platform image")
        if field == "publish_condition":
            publish["if"] = False
        elif field == "publish_continue":
            publish["continue-on-error"] = True
        else:
            publish["run"] = "echo docker buildx imagetools create" if field == "echoed_publish_command" else "true"
    with pytest.raises(AssertionError):
        check_release_manifest_workflow(workflow)


def test_tagged_build_tests_its_registry_digest():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    steps = workflow["jobs"]["build-and-push-image"]["steps"]
    build = next(step["with"] for step in steps if step.get("id") == "build")
    smoke = next(step for step in steps if "KEA_EXPORTER_TEST_IMAGE" in step.get("env", {}))
    assert build["load"] == "${{ inputs.tag == '' }}"
    assert smoke["env"]["KEA_EXPORTER_TEST_IMAGE"] == (
        "${{ inputs.tag != '' && format('{0}/{1}@{2}', env.REGISTRY, env.IMAGE_NAME, steps.build.outputs.digest) "
        "|| format('{0}/{1}', env.REGISTRY, env.IMAGE_NAME) }}"
    )


@pytest.mark.parametrize(
    "function",
    [
        "remove_test_image",
        "docker_daemon",
        "test_image_cleanup_preserves_a_build_failure_before_loading",
        "test_loaded_image_cleanup_preserves_removal_failures",
        "test_cleanup_coverage_depends_on_docker_not_the_registry",
    ],
)
def test_cleanup_commands_bound_operations_instead_of_the_nested_suite(function):
    tree = ast.parse(Path(__file__).read_text())
    target = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
    for call in ast.walk(target):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if not isinstance(call.func.value, ast.Name) or call.func.value.id != "subprocess":
            continue
        timeouts = [keyword.value for keyword in call.keywords if keyword.arg == "timeout"]
        if function == "test_cleanup_coverage_depends_on_docker_not_the_registry":
            assert not timeouts, "The nested pytest run must allow its bounded Docker operations to finish"
        else:
            assert len(timeouts) == 1 and isinstance(timeouts[0], ast.Constant) and timeouts[0].value > 0


def remove_test_image(image):
    found = subprocess.run(
        ["docker", "image", "ls", "--quiet", "--filter", f"reference={image}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if found.stdout.strip():
        subprocess.run(["docker", "image", "rm", image], check=True, capture_output=True, text=True, timeout=30)


@pytest.fixture(scope="module")
def docker_daemon():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("Docker daemon is not available")


@pytest.mark.usefixtures("docker_daemon")
@pytest.mark.parametrize("availability", ["available", "missing_cli", "unavailable_daemon"])
def test_cleanup_coverage_depends_on_docker_not_the_registry(tmp_path, availability):
    environment = dict(os.environ)
    environment.pop("KEA_EXPORTER_TEST_REGISTRY", None)
    if availability == "missing_cli":
        environment["PATH"] = str(tmp_path)
    elif availability == "unavailable_daemon":
        environment.pop("DOCKER_CONTEXT", None)
        environment["DOCKER_HOST"] = f"unix://{tmp_path / 'missing.sock'}"
    report = tmp_path / "cleanup-tests.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            f"--junitxml={report}",
            f"{__file__}::test_image_cleanup_preserves_a_build_failure_before_loading",
            f"{__file__}::test_loaded_image_cleanup_preserves_removal_failures",
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    suite = ET.parse(report).getroot().find("testsuite")
    assert int(suite.attrib["tests"]) == 3
    assert int(suite.attrib["skipped"]) == (0 if availability == "available" else 3)


@pytest.mark.usefixtures("docker_daemon")
def test_image_cleanup_preserves_a_build_failure_before_loading(tmp_path):
    image = f"kea-exporter-cleanup-{uuid4().hex}:test"
    (tmp_path / "Dockerfile").write_text("FROM scratch\nCOPY missing /missing\n")
    with pytest.raises(subprocess.CalledProcessError) as failure:
        try:
            subprocess.run(
                ["docker", "build", "--tag", image, str(tmp_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            remove_test_image(image)
    assert failure.value.cmd[1] == "build", failure.value.stderr
    assert "missing" in failure.value.stderr


@pytest.mark.usefixtures("docker_daemon")
@pytest.mark.parametrize("referenced", [False, True])
def test_loaded_image_cleanup_preserves_removal_failures(tmp_path, referenced):
    name = f"kea-exporter-cleanup-{uuid4().hex}"
    image = f"{name}:test"
    (tmp_path / "Dockerfile").write_text(f'FROM scratch\nLABEL test-image="{name}"\nCMD ["/missing"]\n')
    subprocess.run(
        ["docker", "buildx", "build", "--load", "--tag", image, str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    created = False
    try:
        if referenced:
            subprocess.run(["docker", "create", "--name", name, image], check=True, capture_output=True, timeout=30)
            created = True
            with pytest.raises(subprocess.CalledProcessError) as failure:
                remove_test_image(image)
            assert failure.value.cmd[1:3] == ["image", "rm"]
        else:
            remove_test_image(image)
            images = subprocess.check_output(
                ["docker", "image", "ls", "--quiet", "--filter", f"reference={image}"], timeout=30
            )
            assert not images.strip()
    finally:
        if created:
            subprocess.run(["docker", "rm", name], check=True, capture_output=True, timeout=30)
        remove_test_image(image)


@pytest.mark.skipif(
    not os.environ.get("KEA_EXPORTER_TEST_REGISTRY"), reason="Set a local test registry to test publishing"
)
@pytest.mark.parametrize("oci_media_types", [False, True])
def test_release_manifest_publishes_the_tested_registry_images_for_both_platforms(tmp_path, oci_media_types):
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    steps = workflow["jobs"]["build-and-push-image"]["steps"]
    build = next(step["with"] for step in steps if step.get("id") == "build")
    export = next(step["run"] for step in steps if step.get("id") == "digest")
    publish = workflow["jobs"]["publish-manifests"]["steps"][-1]["run"]
    registry = os.environ["KEA_EXPORTER_TEST_REGISTRY"]
    image_name = f"kea-exporter-workflow-{uuid4().hex}"
    image = f"{registry}/{image_name}"
    tags = [f"{image}:1.2.3-debian", f"{image}:latest-debian", f"{image}:latest"]
    (tmp_path / "Dockerfile").write_text("FROM scratch\nCOPY content /content\n")
    (tmp_path / "content").write_text("Workflow export test\n")
    environment = {
        **os.environ,
        "REGISTRY": registry,
        "IMAGE_NAME": image_name,
        "RUNNER_TEMP": str(tmp_path),
        "DIGEST_DIR": str(tmp_path / "digests"),
        "DOCKER_METADATA_OUTPUT_JSON": json.dumps({"tags": tags}),
    }

    def docker(*arguments):
        result = subprocess.run(
            ["docker", *arguments], capture_output=True, text=True, cwd=tmp_path, timeout=120, check=True
        )
        return result.stdout

    digests = set()
    try:
        for arch in ("amd64", "arm64"):
            docker(
                "buildx",
                "build",
                "--platform",
                build["platforms"].replace("${{ matrix.arch }}", arch),
                "--tag",
                image,
                "--provenance=false",
                "--metadata-file",
                "metadata.json",
                "--output",
                build["outputs"].replace("${{ inputs.tag != '' }}", "true")
                + f",oci-mediatypes={str(oci_media_types).lower()}",
                ".",
            )
            digest = json.loads((tmp_path / "metadata.json").read_text())["containerimage.digest"]
            reference = f"{image}@{digest}"
            docker("pull", reference)
            # Give the owned pull a local tag so the same cleanup covers both export modes.
            docker("tag", reference, image)
            loaded = json.loads(docker("image", "inspect", reference))[0]
            uploaded = json.loads(docker("buildx", "imagetools", "inspect", "--raw", reference))
            assert uploaded["config"]["digest"] == loaded["Id"]
            assert loaded["Architecture"] == arch
            digests.add(digest)
            subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", export],
                env={**environment, "DIGEST": digest, "ARCH": arch},
                check=True,
                timeout=10,
            )
            remove_test_image(image)
        subprocess.run(["bash", "-e", "-o", "pipefail", "-c", publish], env=environment, check=True, timeout=60)
        manifests = [json.loads(docker("buildx", "imagetools", "inspect", "--raw", tag)) for tag in tags]
        assert all(manifest == manifests[0] for manifest in manifests)
        assert {entry["digest"] for entry in manifests[0]["manifests"]} == digests
        assert {entry["platform"]["architecture"] for entry in manifests[0]["manifests"]} == {"amd64", "arm64"}

        for invalid_digest in (None, "invalid"):
            path = tmp_path / "digests" / "arm64"
            if invalid_digest is None:
                path.unlink()
            else:
                path.write_text(invalid_digest + "\n")
            result = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", publish],
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0
    finally:
        remove_test_image(image)
    for tag in tags:
        retained = json.loads(docker("buildx", "imagetools", "inspect", "--raw", tag))
        assert retained == manifests[0]


@pytest.mark.parametrize("matrix_result", ["success", "failure", "cancelled", "skipped"])
def test_required_test_check_accepts_only_a_successful_matrix(matrix_result):
    workflow = yaml.safe_load((ROOT / ".github/workflows/checks.yml").read_text())
    gates = [job for job in workflow["jobs"].values() if job.get("name") == "Run tests"]
    assert len(gates) == 1, "The branch rules require one check named 'Run tests'"
    gate = gates[0]
    assert gate["needs"] == "test"
    assert gate["if"] == "${{ always() }}"
    assert "matrix" in workflow["jobs"]["test"]["strategy"]
    step = gate["steps"][0]
    assert step["env"]["TEST_RESULT"] == "${{ needs.test.result }}"

    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        env={**os.environ, "TEST_RESULT": matrix_result},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert (result.returncode == 0) == (matrix_result == "success"), result.stdout + result.stderr


def test_workflows_pass_security_audit():
    result = subprocess.run(
        ["zizmor", "--offline", ".github/"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("markdownlint-cli2") is None, reason="markdownlint CLI not installed")
def test_markdown_lint_ignores_dependencies_and_checks_project_files(tmp_path):
    shutil.copy(ROOT / ".markdownlint-cli2.yaml", tmp_path)
    dependency = tmp_path / ".venv" / "lib" / "site-packages" / "dependency" / "NOTICES.md"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("```\nDependency notice\n```\n")
    documentation = tmp_path / "README.md"
    documentation.write_text("# Project\n\nValid documentation.\n")

    def lint():
        return subprocess.run(
            ["markdownlint-cli2", "**/*.md"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

    result = lint()
    assert result.returncode == 0, result.stdout + result.stderr

    documentation.write_text("# Project\n\n```\nProject example\n```\n")
    result = lint()
    assert result.returncode != 0
    assert "README.md" in result.stderr
    assert "MD040" in result.stderr
    assert "NOTICES.md" not in result.stderr
