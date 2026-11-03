"""Exercise the lint tools against workflows and dependency files."""

import json
import os
import re
import shlex
import shutil
import subprocess
import tomllib
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


@pytest.mark.parametrize("arch, runner", [("amd64", "ubuntu-24.04"), ("arm64", "ubuntu-24.04-arm")])
def test_each_image_platform_is_built_and_smoke_tested_on_a_native_runner(arch, runner):
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    job = workflow["jobs"]["build-and-push-image"]
    matrix = job["strategy"]["matrix"]
    assert matrix["arch"] == ["amd64", "arm64"]
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
    assert build["load"] is True
    assert "${{ matrix.arch }}" in build["cache-to"]
    test_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("env", {}).get("KEA_EXPORTER_TEST_IMAGE") == build["tags"]
        and "tests/test_docker.py" in step.get("run", "")
    )
    assert steps[test_index]["env"]["KEA_EXPORTER_TEST_PLATFORM"] == build["platforms"]
    digest_index = next(index for index, step in enumerate(steps) if step.get("id") == "digest")
    upload_index = next(
        index for index, step in enumerate(steps) if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert build_index < test_index < digest_index < upload_index


def test_release_manifests_require_all_native_image_checks():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    job = workflow["jobs"]["publish-manifests"]
    assert job["needs"] == "build-and-push-image"
    assert job["if"] == "${{ inputs.tag != '' }}"
    assert job["strategy"]["matrix"]["distro"] == ["alpine", "debian"]
    download = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"]["pattern"] == "digests-${{ matrix.distro }}-*"
    assert download["with"]["merge-multiple"] is True


@pytest.mark.skipif(
    not os.environ.get("KEA_EXPORTER_TEST_REGISTRY"), reason="Set a local test registry to test publishing"
)
def test_release_manifest_publishes_the_loaded_images_for_both_platforms(tmp_path):
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
                "--load",
                "--tag",
                image,
                "--provenance=false",
                "--metadata-file",
                "metadata.json",
                "--output",
                build["outputs"].replace("${{ inputs.tag != '' }}", "true"),
                ".",
            )
            digest = json.loads((tmp_path / "metadata.json").read_text())["containerimage.digest"]
            loaded = json.loads(docker("image", "inspect", image))[0]
            uploaded = json.loads(docker("buildx", "imagetools", "inspect", "--raw", f"{image}@{digest}"))
            assert uploaded["config"]["digest"] == loaded["Id"]
            assert loaded["Architecture"] == arch
            digests.add(digest)
            subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", export],
                env={**environment, "DIGEST": digest, "ARCH": arch},
                check=True,
                timeout=10,
            )
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
        docker("image", "rm", image)


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
