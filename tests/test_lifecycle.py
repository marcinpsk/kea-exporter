"""The stale-label lifecycle on its own.

These drive LabelLifecycle directly, with real Gauge objects in a real
CollectorRegistry, so the timeout and the error paths are reachable without
staging a scrape. test_exporter.py covers the same rules end to end.
"""

import pytest
from prometheus_client import CollectorRegistry, Gauge

from kea_exporter import DHCPVersion
from kea_exporter.lifecycle import LabelLifecycle

SOURCE = ("memory://kea", DHCPVersion.DHCP4)
LABEL_VALUES = (SOURCE[0],)
LABELS = {"server": SOURCE[0]}


@pytest.fixture
def registry():
    return CollectorRegistry()


def prunable_gauge(registry):
    return Gauge("kea_test_prunable", "doc", ("server",), registry=registry)


def test_a_label_absent_from_the_current_cycle_is_pruned_when_its_source_was_scraped(registry):
    lifecycle = LabelLifecycle()
    gauge = prunable_gauge(registry)
    gauge.labels(*LABEL_VALUES).set(1)
    lifecycle.record(gauge, SOURCE, LABEL_VALUES)
    lifecycle.end_cycle({SOURCE}, now=0.0)

    lifecycle.end_cycle({SOURCE}, now=1.0)

    assert registry.get_sample_value("kea_test_prunable", LABELS) is None


def test_a_label_is_kept_while_its_source_is_not_scraped_and_can_be_pruned_later(registry):
    lifecycle = LabelLifecycle()
    gauge = prunable_gauge(registry)
    gauge.labels(*LABEL_VALUES).set(1)
    lifecycle.record(gauge, SOURCE, LABEL_VALUES)
    lifecycle.end_cycle({SOURCE}, now=0.0)

    lifecycle.end_cycle(set(), now=1.0)
    assert registry.get_sample_value("kea_test_prunable", LABELS) == 1

    lifecycle.end_cycle({SOURCE}, now=2.0)
    assert registry.get_sample_value("kea_test_prunable", LABELS) is None


def test_a_label_is_pruned_only_after_the_stale_timeout_passes(registry):
    lifecycle = LabelLifecycle(stale_timeout=60)
    gauge = prunable_gauge(registry)
    gauge.labels(*LABEL_VALUES).set(1)
    lifecycle.record(gauge, SOURCE, LABEL_VALUES)
    lifecycle.end_cycle({SOURCE}, now=0.0)

    lifecycle.end_cycle(set(), now=60.0)
    assert registry.get_sample_value("kea_test_prunable", LABELS) == 1

    lifecycle.end_cycle(set(), now=60.1)
    assert registry.get_sample_value("kea_test_prunable", LABELS) is None


def test_a_label_is_never_pruned_by_time_when_the_timeout_is_disabled(registry):
    lifecycle = LabelLifecycle(stale_timeout=0)
    gauge = prunable_gauge(registry)
    gauge.labels(*LABEL_VALUES).set(1)
    lifecycle.record(gauge, SOURCE, LABEL_VALUES)
    lifecycle.end_cycle({SOURCE}, now=0.0)

    lifecycle.end_cycle(set(), now=9999.0)

    assert registry.get_sample_value("kea_test_prunable", LABELS) == 1


def test_removing_a_label_that_is_already_gone_is_silent(registry, capsys):
    """Prometheus client 0.22 guards absent deletes; see its floor in pyproject.toml."""
    lifecycle = LabelLifecycle()
    gauge = prunable_gauge(registry)
    gauge.labels(*LABEL_VALUES).set(1)
    lifecycle.record(gauge, SOURCE, LABEL_VALUES)
    lifecycle.end_cycle({SOURCE}, now=0.0)
    gauge.remove(*LABEL_VALUES)

    lifecycle.end_cycle({SOURCE}, now=1.0)

    assert capsys.readouterr().err == ""


def test_an_unexpected_removal_error_is_reported_to_stderr(registry, capsys):
    """No production path records a wrong arity, so it stands in for any surprise out of Gauge.remove()."""
    lifecycle = LabelLifecycle()
    gauge = prunable_gauge(registry)
    gauge.labels(*LABEL_VALUES).set(1)
    lifecycle.record(gauge, SOURCE, (SOURCE[0], "too-many"))
    lifecycle.record(gauge, SOURCE, LABEL_VALUES)
    lifecycle.end_cycle({SOURCE}, now=0.0)

    lifecycle.end_cycle({SOURCE}, now=1.0)

    assert "Unexpected error removing gauge label" in capsys.readouterr().err
    assert registry.get_sample_value("kea_test_prunable", LABELS) is None
