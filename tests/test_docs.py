"""Guards for the documentation this repo asks agents and contributors to follow.

Each test here exists because a documented command drifted from the thing it
describes, which no other test could catch.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from kea_exporter import catalogue
from kea_exporter.exporter import Exporter

ROOT = Path(__file__).resolve().parent.parent
AGENT_DOCS = sorted((ROOT / "docs" / "agents").glob("*.md")) + [ROOT / "AGENTS.md"]
ISSUE_TRACKER = ROOT / "docs" / "agents" / "issue-tracker.md"
DOMAIN_GUIDE = ROOT / "docs" / "agents" / "domain.md"
ADR_SCOPE = ROOT / "docs" / "adr" / "0001-scope-drives-metric-labels.md"
CONTEXT = ROOT / "CONTEXT.md"
TEST_README = ROOT / "tests" / "README.md"

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

    readme = TEST_README.read_text()
    documented = re.search(r"^uv run pytest --cov[^\n]*", readme, re.MULTILINE)
    assert documented, "tests/README.md documents no coverage command"

    assert f"--cov-fail-under={ci_threshold.group(1)}" in documented.group(0), (
        f"documented coverage command {documented.group(0)!r} omits the CI gate "
        f"--cov-fail-under={ci_threshold.group(1)}"
    )


def test_test_readme_names_the_test_framework_in_use():
    text = TEST_README.read_text()

    assert "Tests run with `pytest`" in text
    assert "built-in `unittest` framework" not in text


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


def test_no_glossary_definition_uses_a_word_it_tells_you_to_avoid():
    """A definition written in the synonyms it rejects teaches the wrong word."""
    entries = []
    for block in CONTEXT.read_text().split("\n\n"):
        heading = re.match(r"\*\*(?P<term>[^*]+)\*\*:\n(?P<body>.*)", block, re.DOTALL)
        if not heading:
            continue
        avoid = re.search(r"^_Avoid_: (.+)$", heading.group("body"), re.MULTILINE)
        if avoid:
            entries.append((heading.group("term"), heading.group("body")[: avoid.start()], avoid.group(1)))

    assert entries, "no glossary entries with an _Avoid_ list were found; the format changed"

    offences = []
    for term, definition, avoid in entries:
        for word in (w.strip() for w in avoid.split(",")):
            # `s?` so a plural of an avoided word counts as using it.
            if re.search(rf"\b{re.escape(word)}s?\b", definition, re.IGNORECASE):
                offences.append(f"{term}: definition uses {word!r}")

    assert not offences, "CONTEXT.md definitions use terms they tell you to avoid: " + "; ".join(offences)


def test_adr_conflict_example_is_repository_neutral():
    text = DOMAIN_GUIDE.read_text()

    assert "ADR-<number> (<topic>)" in text
    assert "event-sourced orders" not in text


def test_parse_metrics_documents_its_lifecycle_limit():
    docstring = " ".join((Exporter.parse_metrics.__doc__ or "").split())

    assert "without recording lifecycle labels" in docstring
    assert "New label combinations are not eligible for lifecycle pruning" in docstring
    assert "already tracked by update remains eligible" in docstring


def test_adr_0001_states_the_scope_contract_the_exporter_implements():
    """The ADR records why scope drives labels, so it has to match the exporter.

    A reading resolves by an exact (statistic, scope) lookup, and a routine
    global aggregate is suppressed without a report. An ADR that drops either
    half describes a different exporter than the one that ships.
    """
    multi = [e for version in catalogue.CATALOGUE for e in catalogue.CATALOGUE[version] if len(e.scopes) > 1]
    assert multi, "no entry declares several scopes any more; this guard needs rewriting"

    text = ADR_SCOPE.read_text()
    required = {
        "every scope at which the exporter exports": (
            "the rule is the scopes the exporter exports, not the scopes Kea reports: "
            f"{len(multi)} entries declare several, such as {multi[0].statistic!r}, while Kea "
            "also reports aggregates the exporter suppresses"
        ),
        "exact `(statistic, scope)` lookup": "the ADR must say why one scope per entry cannot work",
        "suppressed without a report": "the ADR must keep the exception for routine global aggregates",
    }
    for phrase, reason in required.items():
        assert phrase in text, f"docs/adr/0001 no longer states {phrase!r}: {reason}"

    documented = re.search(r"(\d+) catalogue entries declare more than one scope", text)
    assert documented, "docs/adr/0001 no longer states the multi-scope entry count as a number"
    assert int(documented.group(1)) == len(multi), (
        f"docs/adr/0001 says {documented.group(1)} entries declare several scopes, but the catalogue has {len(multi)}"
    )


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


def test_issue_tracker_gh_examples_are_shell_safe():
    commands = re.findall(r"`(gh [^`]*)`", ISSUE_TRACKER.read_text())

    assert commands, "the issue tracker guide no longer contains gh examples"
    unsafe = [command for command in commands if re.search(r"<[^>]+>", command)]
    assert not unsafe, f"angle-bracket placeholders become shell redirections: {unsafe}"


def test_list_issues_does_not_read_comment_bodies():
    """`gh issue list` returns at most 100 comments per issue; `gh issue view` paginates.

    Both commands expose `comments` as a plain array, so `.comments[].body`
    reads clean and truncates in silence. Only the count gives it away.
    """
    line = bullet(ISSUE_TRACKER.read_text(), "List issues")

    listing = re.search(r"`(gh issue list[^`]*)`", line)
    assert listing, "the List issues bullet no longer shows a `gh issue list` command"
    command = listing.group(1)

    fields = re.search(r"--json ([a-zA-Z,]+)", command)
    assert fields, "the documented `gh issue list` selects no --json fields"
    assert "comments" not in fields.group(1).split(","), (
        "`gh issue list` caps each issue at 100 comments, so listing them here "
        "drops the rest without warning; read bodies with `gh issue view`"
    )
    assert ".comments[" not in command, f"the list command still transforms comment bodies: {command!r}"

    assert 'gh issue view "$issue_number" --json comments' in line, (
        "the bullet must point at `gh issue view` for comment bodies, which pages past 100"
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
    assert 'gh issue view "$child_number" --json' in line, "the frontier query must inspect each child individually"


def test_frontier_query_reads_every_child_and_its_state():
    """A map larger than one page, or a closed child, must not derail the frontier.

    The sub-issues endpoint pages at 30, so an unpaginated call silently
    truncates a large map. Selecting the first *open* child also requires
    asking for state, which is not returned unless requested.
    """
    line = bullet(ISSUE_TRACKER.read_text(), "Frontier query")

    assert "--paginate" in line and "per_page=100" in line, (
        "the sub-issues enumeration is unpaginated, so a map with more than 30 children is silently truncated"
    )

    fields = re.search(r'gh issue view "\$child_number" --json ([a-zA-Z,]+)', line)
    assert fields, 'the frontier query must inspect each child with `gh issue view "$child_number" --json`'
    assert "state" in fields.group(1).split(","), (
        "the query selects the first open child but never requests `state`, "
        f"so openness cannot be read; requested fields are {fields.group(1)}"
    )
    assert '.blockedBy.nodes[] | select(.state == "OPEN")' in line, (
        "the frontier query must test for open blocker nodes, not the blockedBy connection object"
    )


def test_wayfinder_claims_require_one_session_per_map():
    text = ISSUE_TRACKER.read_text()

    assert "Run only one `/wayfinder` session per map." in text
    assert "assignment is not an atomic claim" in text
