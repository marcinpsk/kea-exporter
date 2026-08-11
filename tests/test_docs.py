"""Guards for the documentation this repo asks agents and contributors to follow.

Each test here exists because a documented command drifted from the thing it
describes, which no other test could catch.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT_DOCS = sorted((ROOT / "docs" / "agents").glob("*.md")) + [ROOT / "AGENTS.md"]

# gh subcommands whose --json field list can be checked without network access.
GH_JSON_COMMAND = re.compile(r"gh (issue|pr) (list|view|status)\b[^`\n]*?--json ([a-zA-Z][a-zA-Z,]*)")


def test_documented_coverage_command_matches_ci():
    """tests/README.md must not tell contributors to run a weaker gate than CI."""
    workflow = (ROOT / ".github" / "workflows" / "checks.yml").read_text()
    ci_threshold = re.search(r"--cov-fail-under=(\d+)", workflow)
    assert ci_threshold, "CI no longer sets --cov-fail-under; update this test with it"

    readme = (ROOT / "tests" / "README.md").read_text()
    documented = re.search(r"^uv run pytest --cov[^\n]*", readme, re.MULTILINE)
    assert documented, "tests/README.md documents no coverage command"

    assert f"--cov-fail-under={ci_threshold.group(1)}" in documented.group(0), (
        f"documented coverage command {documented.group(0)!r} omits the CI gate "
        f"--cov-fail-under={ci_threshold.group(1)}"
    )


@pytest.mark.skipif(shutil.which("gh") is None, reason="gh CLI not installed")
@pytest.mark.parametrize("path", AGENT_DOCS, ids=lambda p: p.name)
def test_documented_gh_json_fields_exist(path):
    """Every --json field named in the agent docs must be one gh actually accepts.

    gh prints its field list without contacting the API, so this needs no
    network and no credentials.
    """
    for noun, verb, fields in GH_JSON_COMMAND.findall(path.read_text()):
        listing = subprocess.run(["gh", noun, verb, "--json"], capture_output=True, text=True, timeout=30)
        available = {line.strip() for line in (listing.stdout + listing.stderr).splitlines() if line.startswith("  ")}
        assert available, f"could not read the field list for `gh {noun} {verb}`"

        unknown = sorted(set(fields.split(",")) - available)
        assert not unknown, f"{path.relative_to(ROOT)} documents `gh {noun} {verb} --json` fields gh rejects: {unknown}"
