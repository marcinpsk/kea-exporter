"""Exporter behaviour at the target seam.

Which targets get built, what a scrape does with them, and what happens when
one fails. Every test drives a real Exporter against real targets from
tests.support, so nothing here asserts against a mock of the code under test.
"""

import threading
from queue import Queue

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from kea_exporter import DHCPVersion, catalogue
from kea_exporter.exporter import Exporter
from kea_exporter.http import KeaHTTPClient
from kea_exporter.target import KeaCommandError, SourceFailure
from kea_exporter.uds import KeaSocketClient
from tests.support import FailingTarget, InMemoryTarget, PausingTarget, ScriptedTarget, exporter_with, stat

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


def test_registers_every_catalogued_metric_family(registry):
    """Every Catalogue Metric is visible through the Prometheus registry."""
    Exporter(targets=[], registry=registry)

    families = {family.name: family for family in registry.collect()}
    exposition = exported(registry)
    for version, documentation in catalogue.METRICS.items():
        prefix = catalogue.METRIC_PREFIX[version]
        for metric, expected_documentation in documentation.items():
            metric_name = f"{prefix}_{metric}"
            family = families[metric_name]
            assert family.type == "gauge"
            assert family.documentation == expected_documentation
            assert f"# HELP {metric_name} {expected_documentation}" in exposition
            assert f"# TYPE {metric_name} gauge" in exposition


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

    report = exporter_with(registry, target).update()

    assert registry.get_sample_value("kea_dhcp4_packets_sent_total", {"server": SERVER, "operation": "ack"}) == 10
    assert report.label_combinations_updated == 1
    assert report.stale_labels_removed == 0


def test_one_registry_collection_reads_one_completed_scrape_cycle(registry):
    first = (
        SERVER,
        DHCPVersion.DHCP4,
        {"pkt4-ack-sent": stat(10), "pkt4-discover-received": stat(20)},
        {},
    )
    second = (
        SERVER,
        DHCPVersion.DHCP4,
        {"pkt4-ack-sent": stat(11), "pkt4-discover-received": stat(21)},
        {},
    )
    exporter = exporter_with(registry, ScriptedTarget([first], [second], server_id=SERVER))
    exporter.update()

    collection = iter(registry.collect())
    observed = {}
    sample_names = {"kea_dhcp4_packets_sent_total", "kea_dhcp4_packets_received_total"}
    for family in collection:
        matching = {sample.name: sample.value for sample in family.samples if sample.name in sample_names}
        if matching:
            observed.update(matching)
            break

    exporter.update()
    for family in collection:
        observed.update({sample.name: sample.value for sample in family.samples if sample.name in sample_names})

    assert observed == {
        "kea_dhcp4_packets_sent_total": 10,
        "kea_dhcp4_packets_received_total": 20,
    }


def test_concurrent_update_calls_do_not_overlap_scrape_cycles(registry):
    row = (SERVER, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(10)}, {})
    target = PausingTarget([row], [row], server_id=SERVER)
    exporter = exporter_with(registry, target)
    errors = Queue()
    second_called = threading.Event()

    def update(called=None):
        if called is not None:
            called.set()
        try:
            exporter.update()
        except Exception as error:
            errors.put(error)

    workers = [
        threading.Thread(target=update, daemon=True),
        threading.Thread(target=update, args=(second_called,), daemon=True),
    ]
    workers[0].start()
    assert target.scrape_paused.wait(timeout=15)
    workers[1].start()
    assert second_called.wait(timeout=15)
    try:
        second_scrape_overlapped = target.later_scrape_started.wait(timeout=1)
    finally:
        target.release_scrape.set()
        for worker in workers:
            worker.join(timeout=15)

    assert not [worker for worker in workers if worker.is_alive()], "concurrent Scrape cycles did not finish"
    if not errors.empty():
        raise errors.get()
    assert not second_scrape_overlapped
    assert target.calls == 2


def test_rendering_reads_the_previous_state_until_the_scrape_cycle_completes(registry):
    server_a = "http://kea-a:53100"
    server_b = "http://kea-b:53100"
    first_a = (server_a, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(10)}, {})
    second_a = (server_a, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(11)}, {})
    first_b = (server_b, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(20)}, {})
    second_b = (server_b, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(21)}, {})
    target_a = ScriptedTarget([first_a], [second_a], server_id=server_a)
    target_b = PausingTarget([first_b], [second_b], pause_on_call=2, server_id=server_b)
    exporter = exporter_with(registry, target_a, target_b)
    exporter.update()
    errors = Queue()

    def update():
        try:
            exporter.update()
        except Exception as error:
            errors.put(error)

    worker = threading.Thread(target=update, daemon=True)
    worker.start()
    assert target_b.scrape_paused.wait(timeout=15)
    try:
        labels_a = {"server": server_a, "operation": "ack"}
        labels_b = {"server": server_b, "operation": "ack"}
        observed_while_paused = (
            registry.get_sample_value("kea_dhcp4_packets_sent_total", labels_a),
            registry.get_sample_value("kea_dhcp4_packets_sent_total", labels_b),
        )
    finally:
        target_b.release_scrape.set()
        worker.join(timeout=15)

    assert not worker.is_alive(), "paused Scrape cycle did not finish"
    if not errors.empty():
        raise errors.get()
    assert observed_while_paused == (10, 20)
    assert (
        registry.get_sample_value("kea_dhcp4_packets_sent_total", labels_a),
        registry.get_sample_value("kea_dhcp4_packets_sent_total", labels_b),
    ) == (11, 21)


def test_interval_decision_uses_the_last_direct_scrape_cycle(registry, clock):
    row = (SERVER, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(10)}, {})
    target = ScriptedTarget([row], [row], server_id=SERVER)
    exporter = exporter_with(registry, target)

    exporter.update()
    clock["t"] = 59
    skipped = exporter.update_if_due(60)
    clock["t"] = 60
    completed = exporter.update_if_due(60)

    assert skipped is None
    assert completed is not None
    assert target.calls == 2


def test_concurrent_interval_decisions_start_only_one_scrape_cycle(registry, clock):
    row = (SERVER, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(10)}, {})
    target = PausingTarget([row], [row], [row], pause_on_call=2, server_id=SERVER)
    exporter = exporter_with(registry, target)
    exporter.update()
    clock["t"] = 60
    callers_ready = threading.Barrier(3)
    reports = Queue()
    errors = Queue()

    def update_if_due():
        try:
            callers_ready.wait(timeout=15)
            reports.put(exporter.update_if_due(60))
        except Exception as error:
            errors.put(error)

    workers = [threading.Thread(target=update_if_due, daemon=True) for _ in range(2)]
    for worker in workers:
        worker.start()
    callers_ready.wait(timeout=15)
    assert target.scrape_paused.wait(timeout=15)
    target.release_scrape.set()
    for worker in workers:
        worker.join(timeout=15)

    assert not [worker for worker in workers if worker.is_alive()], "interval decisions did not finish"
    if not errors.empty():
        raise errors.get()
    results = [reports.get(), reports.get()]
    assert sum(report is not None for report in results) == 1
    assert target.calls == 2


def test_interval_decision_waits_for_a_direct_scrape_and_then_rechecks(registry, clock):
    row = (SERVER, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(10)}, {})
    target = PausingTarget([row], [row], [row], pause_on_call=2, server_id=SERVER)
    exporter = exporter_with(registry, target)
    exporter.update()
    clock["t"] = 60
    errors = Queue()
    decision_results = Queue()
    decision_called = threading.Event()

    def direct_update():
        try:
            exporter.update()
        except Exception as error:
            errors.put(error)

    def conditional_update():
        decision_called.set()
        try:
            decision_results.put(exporter.update_if_due(60))
        except Exception as error:
            errors.put(error)

    direct_worker = threading.Thread(target=direct_update, daemon=True)
    direct_worker.start()
    assert target.scrape_paused.wait(timeout=15)
    decision_worker = threading.Thread(target=conditional_update, daemon=True)
    decision_worker.start()
    assert decision_called.wait(timeout=15)
    target.release_scrape.set()
    direct_worker.join(timeout=15)
    decision_worker.join(timeout=15)

    assert not direct_worker.is_alive() and not decision_worker.is_alive(), "Scrape-cycle callers did not finish"
    if not errors.empty():
        raise errors.get()
    assert decision_results.get() is None
    assert target.calls == 2


def test_a_non_numeric_reading_does_not_block_other_readings(registry, capsys):
    initial = (
        SERVER,
        DHCPVersion.DHCP4,
        {"pkt4-ack-sent": stat(10), "pkt4-discover-received": stat(5)},
        {},
    )
    invalid = (
        SERVER,
        DHCPVersion.DHCP4,
        {"pkt4-ack-sent": stat(20), "pkt4-discover-received": stat("not-a-number")},
        {},
    )
    exporter = exporter_with(registry, ScriptedTarget([initial], [invalid], server_id=SERVER))

    exporter.update()
    exporter.update()

    assert (
        registry.get_sample_value(
            "kea_dhcp4_packets_sent_total",
            {"server": SERVER, "operation": "ack"},
        )
        == 20
    )
    assert (
        registry.get_sample_value(
            "kea_dhcp4_packets_received_total",
            {"server": SERVER, "operation": "discover"},
        )
        is None
    )
    assert "value 'not-a-number' is not a number" in capsys.readouterr().err


def test_an_unhandled_statistic_is_reported_only_to_stderr(registry, capsys):
    target = InMemoryTarget(SERVER).add(DHCPVersion.DHCP4, {"invented-statistic": stat(1)})

    exporter_with(registry, target).update()

    output = capsys.readouterr()
    assert "Unhandled statistic 'invented-statistic'" in output.err
    assert output.out == ""


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

    assert capsys.readouterr().err.count("Failed to collect statistics") == 1


def test_recovery_is_announced_and_re_arms_the_report(registry, capsys):
    """The report closes on recovery, so a later outage is reported again."""
    rows = [(SERVER, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(1)}, {})]
    target = ScriptedTarget(ConnectionError("down"), rows, ConnectionError("down again"), server_id=SERVER)
    exporter = exporter_with(registry, target)

    exporter.update()
    exporter.update()
    exporter.update()

    output = capsys.readouterr()
    assert output.err.count("Failed to collect statistics") == 2
    assert f"Collecting statistics from {SERVER} again" in output.err
    assert output.out == ""


def test_a_source_failure_is_reported_once_until_recovery(registry, capsys):
    failure = SourceFailure(SERVER, DHCPVersion.DHCP4, KeaCommandError("DHCP4 unavailable"))
    recovered = (SERVER, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(1)}, {})
    target = ScriptedTarget([failure], [failure], [recovered], [failure], server_id=SERVER)
    exporter = exporter_with(registry, target)

    for _ in range(4):
        exporter.update()

    output = capsys.readouterr()
    assert output.err.count(f"Failed to collect DHCP4 source from {SERVER}") == 2
    assert f"Collecting DHCP4 source from {SERVER} again" in output.err
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


def test_a_successful_empty_source_removes_its_previous_labels(registry):
    row = (SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(5)}, pool_subnets())
    empty = (SERVER, DHCPVersion.DHCP4, {}, pool_subnets())
    exporter = exporter_with(registry, ScriptedTarget([row], [empty], server_id=SERVER))

    exporter.update()
    assert POOL in exported(registry)

    exporter.update()

    assert POOL not in exported(registry)


def test_a_failed_source_keeps_its_labels_until_the_next_success(registry):
    empty = (SERVER, DHCPVersion.DHCP4, {}, pool_subnets())
    target = ScriptedTarget(
        [(SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(7)}, pool_subnets())],
        ConnectionError("target down"),
        [empty],
        server_id=SERVER,
    )
    exporter = exporter_with(registry, target)

    exporter.update()
    exporter.update()

    assert POOL in exported(registry)

    exporter.update()

    assert POOL not in exported(registry)


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

    clock["t"] = 60.0
    exporter.update()
    assert POOL in exported(registry)

    clock["t"] = 60.1
    exporter.update()
    assert POOL not in exported(registry)


def test_the_default_stale_timeout_never_prunes_a_failed_source(registry, clock):
    target = ScriptedTarget(
        [(SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(7)}, pool_subnets())],
        ConnectionError("target down"),
        ConnectionError("target down"),
        server_id=SERVER,
    )
    exporter = exporter_with(registry, target)

    exporter.update()
    clock["t"] = 9999.0
    exporter.update()
    exporter.update()

    assert POOL in exported(registry)


def test_dhcp6_labels_survive_a_scrape_where_that_source_fails(registry):
    pool6 = "2001:db8::10-2001:db8::20"
    subnets6 = {SUBNET_ID: {"subnet": "2001:db8::/64", "pools": [{"pool": pool6}]}}
    row4 = (SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(5)}, pool_subnets())
    changed4 = (SERVER, DHCPVersion.DHCP4, {ASSIGNED: stat(8)}, pool_subnets())
    row6 = (SERVER, DHCPVersion.DHCP6, {"subnet[1].pool[0].assigned-nas": stat(3)}, subnets6)
    failed6 = SourceFailure(SERVER, DHCPVersion.DHCP6, KeaCommandError("DHCP6 unavailable"))

    exporter = exporter_with(registry, ScriptedTarget([row4, row6], [changed4, failed6], server_id=SERVER))

    exporter.update()
    assert POOL in exported(registry) and pool6 in exported(registry)

    exporter.update()
    assert POOL in exported(registry)
    assert pool6 in exported(registry), "DHCP6 pool label was pruned when its source failed"
    assert (
        registry.get_sample_value(
            "kea_dhcp4_addresses_assigned_total",
            {"server": SERVER, "subnet": SUBNET, "subnet_id": "1", "pool": POOL},
        )
        == 8
    )


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
    target = InMemoryTarget(SERVER).add(
        DHCPVersion.DHCP4,
        {"assigned-addresses": stat(123), "pkt4-discover-received": stat(7)},
        subnets={},
    )

    exporter_with(registry, target).update()

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
    target = InMemoryTarget(SERVER).add(
        DHCPVersion.DHCP6,
        {"assigned-nas": stat(10), "assigned-pds": stat(5), "pkt6-solicit-received": stat(3)},
        subnets={},
    )

    exporter_with(registry, target).update()

    assert (
        registry.get_sample_value("kea_dhcp6_packets_received_total", {"server": SERVER, "operation": "solicit"}) == 3
    )


def test_the_new_pkt4_counters_are_exported(registry):
    target = InMemoryTarget(SERVER).add(
        DHCPVersion.DHCP4,
        {
            "pkt4-lease-query-received": stat(1),
            "pkt4-lease-query-response-active-sent": stat(2),
            "pkt4-duplicate": stat(3),
            "pkt4-service-disabled": stat(4),
        },
        subnets={},
    )

    exporter_with(registry, target).update()

    sample = registry.get_sample_value
    assert sample("kea_dhcp4_packets_received_total", {"server": SERVER, "operation": "lease-query"}) == 1
    assert sample("kea_dhcp4_packets_sent_total", {"server": SERVER, "operation": "lease-query-response-active"}) == 2
    assert sample("kea_dhcp4_packets_received_total", {"server": SERVER, "operation": "duplicate"}) == 3
    assert sample("kea_dhcp4_packets_received_total", {"server": SERVER, "operation": "service-disabled"}) == 4


def test_the_new_pkt6_counters_are_exported(registry):
    target = InMemoryTarget(SERVER).add(
        DHCPVersion.DHCP6,
        {
            "pkt6-lease-query-received": stat(1),
            "pkt6-lease-query-reply-sent": stat(2),
            "pkt6-queue-full": stat(5),
        },
        subnets={},
    )

    exporter_with(registry, target).update()

    sample = registry.get_sample_value
    assert sample("kea_dhcp6_packets_received_total", {"server": SERVER, "operation": "lease-query"}) == 1
    assert sample("kea_dhcp6_packets_sent_total", {"server": SERVER, "operation": "lease-query-reply"}) == 2
    assert sample("kea_dhcp6_packets_received_total", {"server": SERVER, "operation": "queue-full"}) == 5
