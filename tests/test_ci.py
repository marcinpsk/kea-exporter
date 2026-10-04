"""Exercise the lint tools against workflows and dependency files."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


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
