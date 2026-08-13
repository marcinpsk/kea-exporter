"""Exporter behaviour at the target seam.

Which targets get built, what a scrape does with them, and what happens when
one fails. Every test drives a real Exporter against real targets from
tests.support, so nothing here asserts against a mock of the code under test.
"""

import pytest
from prometheus_client import CollectorRegistry, generate_latest

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
    """Every metric the catalogue declares is registered, for every daemon.

    The exported names are read back out of the registry rather than off the
    exporter's own dictionary, so a wrong prefix cannot pass.
    """
    exporter = Exporter(targets=[], registry=registry)
    exposition = generate_latest(registry).decode()

    for version, documentation in catalogue.METRICS.items():
        assert set(exporter.metrics[version]) == set(documentation), version.name
        prefix = catalogue.METRIC_PREFIX[version]
        for metric, gauge in exporter.metrics[version].items():
            entries = catalogue.entries_by_metric(version)[metric]
            labelnames = catalogue.labelnames(entries)
            assert exporter.metric_labelnames[version][metric] == labelnames, f"{version.name}:{metric}"
            gauge.labels(**dict.fromkeys(labelnames, ""))
            assert f"# HELP {prefix}_{metric} " in exposition, f"{version.name}:{metric} is not registered as such"


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
    output = capsys.readouterr()
    assert "Unable to parse target argument" in output.err
    assert output.out == ""


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
    output = capsys.readouterr()
    assert "Failed to initialize target" in output.err
    assert "admin" not in output.err and "s3cret" not in output.err
    assert "kea.local:8000" in output.err
    assert output.out == ""


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
    assert f"Collecting metrics from {SERVER} again" in output.err
    assert output.out == ""


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


def test_a_standalone_parse_does_not_keep_a_label_omitted_by_the_next_scrape(registry):
    row = (SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(5)}, pool_subnets())
    empty = (SERVER, DHCPVersion.DHCP4, {}, pool_subnets())
    exporter = exporter_with(registry, ScriptedTarget([row], [empty], server_id=SERVER))

    exporter.update()
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(7)}, pool_subnets())
    exporter.update()

    assert POOL not in exported(registry)


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


def test_a_scraped_source_prunes_its_stale_label_without_pruning_a_failed_source(registry):
    server_a = "http://kea-a:53100"
    server_b = "http://kea-b:53100"
    new_pool = "10.0.0.100-10.0.0.110"
    pool_b = "10.1.0.100-10.1.0.200"
    row_a1 = (server_a, DHCPVersion.DHCP4, {ASSIGNED: stat(5)}, pool_subnets())
    row_a2 = (server_a, DHCPVersion.DHCP4, {ASSIGNED: stat(3)}, pool_subnets(pool=new_pool))
    row_b = (
        server_b,
        DHCPVersion.DHCP4,
        {ASSIGNED: stat(7)},
        pool_subnets(subnet="10.1.0.0/24", pool=pool_b),
    )
    target_a = ScriptedTarget([row_a1], [row_a2], server_id=server_a)
    target_b = ScriptedTarget([row_b], ConnectionError("target down"), server_id=server_b)
    exporter = exporter_with(registry, target_a, target_b)

    exporter.update()
    exporter.update()

    exposition = exported(registry)
    assert POOL not in exposition
    assert new_pool in exposition
    assert pool_b in exposition


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
    assert Exporter(targets=[], registry=registry).lifecycle.stale_timeout == 0


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


def test_no_source_from_a_target_is_scraped_when_a_later_row_fails_to_parse(registry):
    new_pool = "10.0.0.100-10.0.0.110"
    original = (SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(5)}, pool_subnets())
    renamed = (SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(3)}, pool_subnets(pool=new_pool))
    invalid = (SERVER, DHCPVersion.DHCP6, None, {})
    exporter = exporter_with(registry, ScriptedTarget([original], [renamed, invalid], server_id=SERVER))

    exporter.update()
    exporter.update()

    exposition = exported(registry)
    assert POOL in exposition
    assert new_pool not in exposition


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
