"""Exercise the lint tools against workflows and dependency files."""

import os
import re
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path

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


@pytest.mark.parametrize("platform", ["linux/amd64", "linux/arm64"])
def test_each_published_platform_is_smoke_tested_before_publication(platform):
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    steps = workflow["jobs"]["build-and-push-image"]["steps"]
    qemu_index = next(
        index for index, step in enumerate(steps) if step.get("uses", "").startswith("docker/setup-qemu-action@")
    )
    builds = [
        (index, step["with"])
        for index, step in enumerate(steps)
        if step.get("with", {}).get("platforms") == platform and step["with"].get("load") is True
    ]
    assert len(builds) == 1, f"Build and load a {platform} image for runtime smoke tests"
    build_index, build = builds[0]
    test_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("env", {}).get("KEA_EXPORTER_TEST_IMAGE") == build["tags"]
        and "tests/test_docker.py" in step.get("run", "")
    )
    assert steps[test_index]["env"]["KEA_EXPORTER_TEST_PLATFORM"] == platform
    publish_index = next(index for index, step in enumerate(steps) if step.get("with", {}).get("push"))
    assert qemu_index < build_index < test_index < publish_index


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
