"""What semantic-release puts in front of users.

CHANGELOG.md and the GitHub release render from the same excluded set, so a
commit type that is not user-facing has to be excluded once, in pyproject.
"""

import re
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
RELEASE = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["semantic_release"]
PARSER = RELEASE["commit_parser_options"]

USER_FACING = set(PARSER["minor_tags"]) | set(PARSER["patch_tags"])
EXCLUDED = [re.compile(pattern) for pattern in RELEASE["changelog"]["exclude_commit_patterns"]]


@pytest.mark.parametrize("tag", PARSER["allowed_tags"])
@pytest.mark.parametrize("subject", ["{tag}: a change", "{tag}(scope): a change"])
def test_every_commit_type_is_either_released_or_excluded(tag, subject):
    """A new allowed_tag must be a decision, not a silent release-note entry."""
    excluded = any(pattern.match(subject.format(tag=tag)) for pattern in EXCLUDED)

    if tag in USER_FACING:
        assert not excluded, f"{tag!r} bumps the version but is excluded from the release notes"
    else:
        assert excluded, (
            f"{tag!r} is an allowed tag, does not bump the version, and is not excluded, "
            "so it would appear in CHANGELOG.md and in the GitHub release"
        )


def test_the_changelog_template_is_where_semantic_release_looks():
    """`template_dir` selects the templates; `default_templates` has no `template` key.

    semantic-release ignores keys it does not know, so naming the template
    there reads as configuration while changing nothing.
    """
    changelog = RELEASE["changelog"]

    assert "template" not in changelog.get("default_templates", {}), (
        "default_templates.template is not a semantic-release setting, so it is silently "
        "ignored; the template directory is set with changelog.template_dir"
    )

    template_dir = ROOT / changelog.get("template_dir", "templates")
    assert (template_dir / "CHANGELOG.md.j2").is_file(), f"no changelog template in {template_dir}"
