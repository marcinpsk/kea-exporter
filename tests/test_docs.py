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
ISSUE_TRACKER = ROOT / "docs" / "agents" / "issue-tracker.md"

# gh subcommands whose --json field list can be checked without network access.
GH_JSON_COMMAND = re.compile(r"gh (issue|pr) (list|view|status)\b[^`\n]*?--json ([a-zA-Z][a-zA-Z,]*)")

# GitHub's author_association enum. Everything that is not one of these three is
# somebody outside the repo, so triage filters on the three rather than listing
# the rest: GitHub has added values (FIRST_TIMER, MANNEQUIN) and may add more.
INTERNAL_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
EXTERNAL_ASSOCIATIONS = {"CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "MANNEQUIN", "NONE"}


def bullet(text, label):
    """The single documentation bullet whose bolded label matches."""
    for line in text.splitlines():
        if line.startswith(f"- **{label}**"):
            return line
    raise AssertionError(f"no bullet labelled {label!r}")


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


def test_external_pr_filter_excludes_insiders_rather_than_listing_outsiders():
    """A positive list of external associations silently drops anything GitHub adds.

    author_association has eight values; FIRST_TIMER and MANNEQUIN are both
    outsiders. Filtering by "not one of the three insider values" cannot miss a
    new one, which a hand-listed set of outsiders would.
    """
    line = bullet(ISSUE_TRACKER.read_text(), "List external PRs for triage")

    assert "author_association" in line, "the triage filter no longer reads author_association"
    named = set(re.findall(r'"([A-Z_]+)"', line))

    assert INTERNAL_ASSOCIATIONS <= named, (
        f"filter must name the insider associations it drops; missing {sorted(INTERNAL_ASSOCIATIONS - named)}"
    )
    assert not named & EXTERNAL_ASSOCIATIONS, (
        "filter enumerates outsider associations "
        f"{sorted(named & EXTERNAL_ASSOCIATIONS)}; exclude insiders instead so new "
        "association values are not silently dropped"
    )


def test_frontier_query_does_not_list_the_whole_repository():
    """gh issue list is repo-wide and caps at 30, so it cannot walk a map's children in order.

    Naming the command to warn against it is fine; invoking it is not, so this
    looks for an invocation with arguments rather than any mention.
    """
    line = bullet(ISSUE_TRACKER.read_text(), "Frontier query")

    assert not re.search(r"gh issue list\s+-", line), (
        "the frontier query invokes `gh issue list`, which queries every issue in the "
        "repository, defaults to 30 results and loses map order; enumerate the map's "
        "children first, then inspect each with `gh issue view`"
    )
    assert "gh issue view <child> --json" in line, "the frontier query must inspect each child individually"
