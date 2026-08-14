"""The README's metric table is derived from the catalogue, so it cannot drift.

Adding or renaming a metric without updating the README fails here, with the
correct block in the failure message.
"""

from pathlib import Path

from kea_exporter import DHCPVersion, catalogue

README = Path(__file__).resolve().parent.parent / "README.rst"
START = ".. metrics-table-start"
END = ".. metrics-table-end"

DAEMONS = [(DHCPVersion.DHCP4, "DHCPv4"), (DHCPVersion.DHCP6, "DHCPv6"), (DHCPVersion.DDNS, "DDNS")]


def render_table(version):
    rows = sorted(
        (f"{catalogue.METRIC_PREFIX[version]}_{metric}", ", ".join(catalogue.labelnames(entries)))
        for metric, entries in catalogue.entries_by_metric(version).items()
    )
    width = max(len("Metric"), *(len(name) for name, _ in rows))
    labels_width = max(len("Labels"), *(len(labels) for _, labels in rows))
    rule = f"{'=' * width}  {'=' * labels_width}"
    lines = [rule, f"{'Metric'.ljust(width)}  Labels", rule]
    lines += [f"{name.ljust(width)}  {labels}" for name, labels in rows]
    lines.append(rule)
    return "\n".join(lines)


def render_section():
    return "\n\n".join(f"{title}\n{'/' * len(title)}\n\n{render_table(version)}" for version, title in DAEMONS)


def documented_section():
    text = README.read_text()
    start = text.index(START) + len(START)
    return text[start : text.index(END)].strip("\n")


def test_readme_metric_table_matches_the_catalogue():
    expected = render_section()
    if documented_section() != expected:
        raise AssertionError(
            f"README.rst metric table is out of date. Replace the block between {START} and {END} with:\n\n{expected}"
        )


def test_readme_documents_every_exported_metric():
    text = README.read_text()
    for version in catalogue.CATALOGUE:
        for metric in catalogue.METRICS[version]:
            assert f"{catalogue.METRIC_PREFIX[version]}_{metric}" in text, metric


def test_upgrade_notes_warn_that_diagnostics_moved_to_stderr():
    text = README.read_text()
    start = text.index("Upgrading from 0.9")
    end = text.index("Known Limitations", start)
    upgrade_notes = text[start:end]

    assert "lifecycle and unhandled-statistic diagnostics" in upgrade_notes.lower()
    assert "stderr" in upgrade_notes
    assert "stdout" in upgrade_notes
