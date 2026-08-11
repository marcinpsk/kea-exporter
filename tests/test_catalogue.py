"""The catalogue's own guard rails.

validate() runs at import, so a catalogue that cannot produce consistent
metrics fails at start-up rather than mid-scrape. These tests feed it broken
tables to prove the guard actually holds.
"""

import pytest

from kea_exporter import DHCPVersion
from kea_exporter.catalogue import (
    CATALOGUE,
    METRICS,
    NEVER_EXPORT,
    CatalogueError,
    Entry,
    Scope,
    labelnames,
    validate,
    validate_daemon,
)

GLOBAL = frozenset({Scope.GLOBAL})
SUBNET = frozenset({Scope.SUBNET})
POOL = frozenset({Scope.POOL})


def check(entries, documentation=None, never=frozenset()):
    documentation = {e.metric: "help" for e in entries} if documentation is None else documentation
    validate_daemon("test", entries, documentation, never)


def test_the_shipped_catalogue_is_valid():
    validate()


def test_entry_naming_an_undeclared_metric_is_rejected():
    with pytest.raises(CatalogueError, match="not declared"):
        check([Entry("a-stat", "missing_total", GLOBAL)], documentation={})


def test_metric_no_entry_maps_to_is_rejected():
    """A declared metric nothing routes to would emit bare HELP and TYPE lines forever."""
    with pytest.raises(CatalogueError, match="no entry maps to"):
        check([Entry("a-stat", "used_total", GLOBAL)], documentation={"used_total": "h", "orphan_total": "h"})


def test_entries_disagreeing_on_labels_are_rejected():
    """Two statistics on one metric must agree, or one of them cannot fill its labels."""
    entries = [Entry("a-stat", "shared_total", GLOBAL), Entry("b-stat", "shared_total", SUBNET)]
    with pytest.raises(CatalogueError, match="disagree on labels"):
        check(entries)


def test_entry_overriding_a_scope_label_is_rejected():
    """Scope owns subnet, pool and friends; a static label must not shadow one."""
    with pytest.raises(CatalogueError, match="which scope already provides"):
        check([Entry("a-stat", "a_total", SUBNET, {"subnet": "hardcoded"})])


def test_statistic_both_exported_and_never_exported_is_rejected():
    with pytest.raises(CatalogueError, match="both exported and never-exported"):
        check([Entry("a-stat", "a_total", GLOBAL)], never=frozenset({"a-stat"}))


def test_sibling_scopes_on_one_metric_are_allowed():
    """reclaimed-leases is reported under both pool kinds, on one metric."""
    both = frozenset({Scope.SUBNET, Scope.POOL, Scope.PD_POOL})
    check([Entry("reclaimed-leases", "reclaimed_total", both)])


def test_labels_follow_a_stable_order():
    """Label order must not depend on set iteration, or metrics churn between runs."""
    entries = [Entry("a-stat", "a_total", frozenset({Scope.POOL, Scope.PD_POOL}), {"context": "x"})]
    assert labelnames(entries) == ("server", "subnet", "subnet_id", "pool", "pd_pool", "context")


def test_no_statistic_is_exported_and_never_exported_in_the_shipped_tables():
    for version in CATALOGUE:
        exported = {entry.statistic for entry in CATALOGUE[version]}
        assert not exported & NEVER_EXPORT[version], version.name


def test_ddns_reuses_one_statistic_name_at_two_scopes():
    """update-sent is reported globally and per key, onto two different metrics."""
    entries = [e for e in CATALOGUE[DHCPVersion.DDNS] if e.statistic == "update-sent"]
    assert {e.metric for e in entries} == {"update_sent_total", "key_update_sent_total"}
    assert {next(iter(e.scopes)) for e in entries} == {Scope.GLOBAL, Scope.DDNS_KEY}


def test_every_declared_metric_has_documentation():
    for version, documentation in METRICS.items():
        assert all(text for text in documentation.values()), version.name
