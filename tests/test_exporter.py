"""Exporter behaviour at the target seam.

Which targets get built, what a scrape does with them, and what happens when
one fails. Every test drives a real Exporter against real targets from
tests.support, so nothing here asserts against a mock of the code under test.
"""

import pytest
from prometheus_client import CollectorRegistry, Gauge, generate_latest

from kea_exporter import DHCPVersion, catalogue
from kea_exporter.exporter import Exporter
from kea_exporter.http import KeaHTTPClient
from kea_exporter.uds import KeaSocketClient
from tests.support import FailingTarget, InMemoryTarget, ScriptedTarget, exporter_with, stat

SERVER = "http://kea-dhcp4:53100"
SUBNET_ID = 1
SUBNET = "10.0.0.0/24"
POOL = "10.0.0.100-10.0.0.200"

ASSIGNED = "subnet[1].pool[0].assigned-addresses"


def pool_subnets(subnet=SUBNET, pool=POOL):
    return {SUBNET_ID: {"subnet": subnet, "pools": [{"pool": pool}]}}


@pytest.fixture
def registry():
    return CollectorRegistry()


@pytest.fixture
def clock(monkeypatch):
    """Control the monotonic clock the stale-label timeout reads."""
    now = {"t": 0.0}
    monkeypatch.setattr("kea_exporter.exporter.time.monotonic", lambda: now["t"])
    return now


def exported(registry):
    return generate_latest(registry).decode()


# ------------------------------------------------------------------ building targets


def test_builds_a_gauge_for_every_catalogued_metric(registry):
    """Every metric the catalogue declares is registered, for every daemon."""
    exporter = Exporter(targets=[], registry=registry)

    for version, documentation in catalogue.METRICS.items():
        assert set(exporter.metrics[version]) == set(documentation), version.name
        for metric, gauge in exporter.metrics[version].items():
            entries = catalogue.entries_by_metric(version)[metric]
            assert gauge._labelnames == catalogue.labelnames(entries), f"{version.name}:{metric}"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("http://localhost:8000", KeaHTTPClient),
        ("/var/run/kea/control.sock", KeaSocketClient),
    ],
)
def test_the_target_string_chooses_the_adapter(registry, target, expected):
    """Construction performs no I/O, so this needs no server and no socket."""
    exporter = Exporter(targets=[target], registry=registry)

    assert len(exporter.targets) == 1
    assert isinstance(exporter.targets[0], expected)


def test_every_target_is_built(registry):
    exporter = Exporter(
        targets=["http://localhost:8000", "/var/run/kea/socket1", "http://remote:8001"],
        registry=registry,
    )

    assert len(exporter.targets) == 3


def test_timeout_reaches_the_adapter(registry):
    exporter = Exporter(targets=["http://localhost:8000"], timeout=30, registry=registry)

    assert exporter.targets[0].timeout == 30


def test_an_unparseable_target_is_reported_and_dropped(registry, capsys):
    exporter = Exporter(targets=[""], registry=registry)

    assert exporter.targets == []
    assert "Unable to parse target argument" in capsys.readouterr().out


def test_a_misconfigured_target_is_dropped_rather_than_retried(registry, capsys):
    """Construction only fails on configuration, which retrying cannot fix.

    Mutual TLS with a certificate and no key raises before any connection.
    """
    exporter = Exporter(
        targets=["http://admin:s3cret@kea.local:8000/api"],
        registry=registry,
        client_cert="/nonexistent/cert.pem",
    )

    assert exporter.targets == []
    out = capsys.readouterr().out
    assert "Failed to initialize target" in out
    assert "admin" not in out and "s3cret" not in out
    assert "kea.local:8000" in out


# ------------------------------------------------------------------ scraping


def test_update_scrapes_every_target(registry):
    first = InMemoryTarget("http://server1:8000").add(DHCPVersion.DHCP4, {})
    second = InMemoryTarget("http://server2:8000").add(DHCPVersion.DHCP6, {})

    exporter_with(registry, first, second).update()

    assert (first.calls, second.calls) == (1, 1)


def test_update_exports_what_a_target_reports(registry):
    target = InMemoryTarget(SERVER).add(DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(10)})

    exporter_with(registry, target).update()

    assert registry.get_sample_value("kea_dhcp4_packets_sent_total", {"server": SERVER, "operation": "ack"}) == 10


def test_one_failing_target_does_not_stop_the_others(registry, capsys):
    down = FailingTarget("http://server1:8000", ConnectionError("server1 down"))
    up = InMemoryTarget("http://server2:8000").add(DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(4)})

    exporter_with(registry, down, up).update()

    assert up.calls == 1
    assert "server1" in capsys.readouterr().err
    assert (
        registry.get_sample_value("kea_dhcp4_packets_sent_total", {"server": "http://server2:8000", "operation": "ack"})
        == 4
    )


# ------------------------------------------------------------------ reporting a failure


def test_a_target_that_stays_down_is_reported_once(registry, capsys):
    """A host down for hours must not write a line on every scrape."""
    exporter = exporter_with(registry, FailingTarget(SERVER, ConnectionError("down")))

    for _ in range(5):
        exporter.update()

    assert capsys.readouterr().err.count("Failed to collect metrics") == 1


def test_recovery_is_announced_and_re_arms_the_report(registry, capsys):
    """The report closes on recovery, so a later outage is reported again."""
    rows = [(SERVER, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(1)}, {})]
    target = ScriptedTarget(ConnectionError("down"), rows, ConnectionError("down again"), server_id=SERVER)
    exporter = exporter_with(registry, target)

    exporter.update()
    exporter.update()
    exporter.update()

    output = capsys.readouterr()
    assert output.err.count("Failed to collect metrics") == 2
    assert f"Collecting metrics from {SERVER} again" in output.out


# ------------------------------------------------------------------ stale labels


def test_renamed_pool_label_is_removed_on_the_next_scrape(registry):
    new_pool = "10.0.0.100-10.0.0.110"
    target = ScriptedTarget(
        [(SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(5)}, pool_subnets())],
        [(SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(3)}, pool_subnets(pool=new_pool))],
        server_id=SERVER,
    )
    exporter = exporter_with(registry, target)

    exporter.update()
    assert POOL in exported(registry)

    exporter.update()
    assert POOL not in exported(registry)
    assert new_pool in exported(registry)


def test_a_label_is_kept_when_the_scrape_fails(registry):
    target = ScriptedTarget(
        [(SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(7)}, pool_subnets())],
        ConnectionError("target down"),
        server_id=SERVER,
    )
    exporter = exporter_with(registry, target)

    exporter.update()
    exporter.update()

    assert POOL in exported(registry)


def test_a_label_is_pruned_once_the_stale_timeout_passes(registry, clock):
    """success, then a failure inside the timeout, then one past it."""
    target = ScriptedTarget(
        [(SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(7)}, pool_subnets())],
        ConnectionError("target down"),
        ConnectionError("target down"),
        server_id=SERVER,
    )
    exporter = exporter_with(registry, target, stale_timeout=60)

    exporter.update()
    assert POOL in exported(registry)

    clock["t"] = 30.0
    exporter.update()
    assert POOL in exported(registry)

    clock["t"] = 61.0
    exporter.update()
    assert POOL not in exported(registry)


def test_a_label_is_never_pruned_when_the_timeout_is_disabled(registry, clock):
    target = ScriptedTarget(
        [(SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(7)}, pool_subnets())],
        ConnectionError("target down"),
        ConnectionError("target down"),
        server_id=SERVER,
    )
    exporter = exporter_with(registry, target, stale_timeout=0)

    exporter.update()
    clock["t"] = 9999.0
    exporter.update()
    exporter.update()

    assert POOL in exported(registry)


def test_the_stale_timeout_is_off_by_default(registry):
    assert Exporter(targets=[], registry=registry).stale_timeout == 0


def test_dhcp6_labels_survive_a_scrape_that_only_covers_dhcp4(registry):
    """A daemon missing from one scrape must not lose its series."""
    pool6 = "2001:db8::10-2001:db8::20"
    subnets6 = {SUBNET_ID: {"subnet": "2001:db8::/64", "pools": [{"pool": pool6}]}}
    row4 = (SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(5)}, pool_subnets())
    row6 = (SERVER, DHCPVersion.DHCP6, {"subnet[1].pool[0].assigned-nas": stat(3)}, subnets6)

    exporter = exporter_with(registry, ScriptedTarget([row4, row6], [row4], server_id=SERVER))

    exporter.update()
    assert POOL in exported(registry) and pool6 in exported(registry)

    exporter.update()
    assert POOL in exported(registry)
    assert pool6 in exported(registry), "dhcp6 pool label was pruned when only dhcp4 was scraped"


# ------------------------------------------------------------------ pruning failures


def mark_stale(exporter, gauge, label_tuple):
    """Offer a gauge to the pruner with no `server` label.

    Without one the pruner cannot tell whether the scrape succeeded, so it
    always calls remove() and the error paths are reachable.
    """
    exporter._seen_labels_previous = {id(gauge): (gauge, {label_tuple})}


def test_removing_a_label_that_is_already_gone_is_silent(registry, capsys):
    """prometheus-client >= 0.22 guards the delete, which is why the floor is 0.22."""
    exporter = exporter_with(registry)
    gauge = Gauge("kea_test_prunable", "doc", ("operation",), registry=registry)
    gauge.labels(operation="kept").set(1)
    mark_stale(exporter, gauge, ("never-set",))

    exporter.update()

    assert capsys.readouterr().err == ""
    assert registry.get_sample_value("kea_test_prunable", {"operation": "kept"}) == 1


def test_an_unexpected_removal_error_is_logged(registry, capsys):
    """A real Gauge raises ValueError when the label count is wrong."""
    exporter = exporter_with(registry)
    gauge = Gauge("kea_test_prunable", "doc", ("operation",), registry=registry)
    mark_stale(exporter, gauge, ("one", "too-many"))

    exporter.update()

    assert "Unexpected error removing gauge label" in capsys.readouterr().err


# ------------------------------------------------------------------ Kea 3.2 statistics


def test_a_global_aggregate_does_not_abort_a_dhcp4_scrape(registry):
    """assigned-addresses arrives globally in 3.2 and maps to a subnet gauge."""
    exporter = exporter_with(registry)

    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP4,
        {"assigned-addresses": stat(123), "pkt4-discover-received": stat(7)},
        subnets={},
    )

    assert (
        registry.get_sample_value("kea_dhcp4_packets_received_total", {"server": SERVER, "operation": "discover"}) == 7
    )
    assert (
        registry.get_sample_value(
            "kea_dhcp4_addresses_assigned_total",
            {"server": SERVER, "subnet": "", "subnet_id": "", "pool": ""},
        )
        is None
    )


def test_global_nas_and_pds_do_not_abort_a_dhcp6_scrape(registry):
    exporter = exporter_with(registry)

    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP6,
        {"assigned-nas": stat(10), "assigned-pds": stat(5), "pkt6-solicit-received": stat(3)},
        subnets={},
    )

    assert (
        registry.get_sample_value("kea_dhcp6_packets_received_total", {"server": SERVER, "operation": "solicit"}) == 3
    )


def test_the_new_pkt4_counters_are_exported(registry):
    exporter = exporter_with(registry)

    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP4,
        {
            "pkt4-lease-query-received": stat(1),
            "pkt4-lease-query-response-active-sent": stat(2),
            "pkt4-duplicate": stat(3),
            "pkt4-service-disabled": stat(4),
        },
        subnets={},
    )

    sample = registry.get_sample_value
    assert sample("kea_dhcp4_packets_received_total", {"server": SERVER, "operation": "lease-query"}) == 1
    assert sample("kea_dhcp4_packets_sent_total", {"server": SERVER, "operation": "lease-query-response-active"}) == 2
    assert sample("kea_dhcp4_packets_received_total", {"server": SERVER, "operation": "duplicate"}) == 3
    assert sample("kea_dhcp4_packets_received_total", {"server": SERVER, "operation": "service-disabled"}) == 4


def test_the_new_pkt6_counters_are_exported(registry):
    exporter = exporter_with(registry)

    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP6,
        {
            "pkt6-lease-query-received": stat(1),
            "pkt6-lease-query-reply-sent": stat(2),
            "pkt6-queue-full": stat(5),
        },
        subnets={},
    )

    sample = registry.get_sample_value
    assert sample("kea_dhcp6_packets_received_total", {"server": SERVER, "operation": "lease-query"}) == 1
    assert sample("kea_dhcp6_packets_sent_total", {"server": SERVER, "operation": "lease-query-reply"}) == 2
    assert sample("kea_dhcp6_packets_received_total", {"server": SERVER, "operation": "queue-full"}) == 5
