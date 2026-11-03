"""Exercise the lint tools against workflows and dependency files."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


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
