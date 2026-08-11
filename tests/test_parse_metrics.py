"""Routing: which Kea statistic lands on which metric, with which labels.

Every assertion here reads the exported series out of a real
CollectorRegistry, so a change to a metric's declared labels shows up as a
failure rather than passing against a mirrored copy of the label list.
"""

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from kea_exporter import DHCPVersion, catalogue
from tests.support import exporter_with, samples, stat, subnet4, subnet6

SERVER = "memory://kea"


@pytest.fixture
def registry():
    return CollectorRegistry()


@pytest.fixture
def exporter(registry):
    return exporter_with(registry)


def sample(registry, name, **labels):
    return registry.get_sample_value(name, labels)


# ---------------------------------------------------------------- packets


def test_dhcp4_packet_counters(exporter, registry):
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP4,
        {"pkt4-ack-sent": stat(10), "pkt4-discover-received": stat(20)},
        {},
    )
    assert sample(registry, "kea_dhcp4_packets_sent_total", server=SERVER, operation="ack") == 10
    assert sample(registry, "kea_dhcp4_packets_received_total", server=SERVER, operation="discover") == 20


def test_metric_updates_use_only_the_public_gauge_interface(exporter):
    class PublicGauge:
        def labels(self, **labels):
            self.labels_seen = labels
            return self

        def set(self, value):
            self.value = value

    gauge = PublicGauge()
    exporter.metrics[DHCPVersion.DHCP4]["packets_sent_total"] = gauge

    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"pkt4-ack-sent": stat(10)}, {})

    assert gauge.labels_seen == {"server": SERVER, "operation": "ack"}
    assert gauge.value == 10


def test_dhcp6_packet_counters(exporter, registry):
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP6,
        {"pkt6-reply-sent": stat(11), "pkt6-solicit-received": stat(21)},
        {},
    )
    assert sample(registry, "kea_dhcp6_packets_sent_total", server=SERVER, operation="reply") == 11
    assert sample(registry, "kea_dhcp6_packets_received_total", server=SERVER, operation="solicit") == 21


def test_dhcp6_address_registration_packets(exporter, registry):
    """Address registration counters, Kea 3.0.0."""
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP6,
        {
            "pkt6-addr-reg-reply-sent": stat(3),
            "pkt6-addr-reg-inform-received": stat(4),
            "pkt6-addr-reg-reply-received": stat(0),
        },
        {},
    )
    assert sample(registry, "kea_dhcp6_packets_sent_total", server=SERVER, operation="addr-reg-reply") == 3
    assert sample(registry, "kea_dhcp6_packets_received_total", server=SERVER, operation="addr-reg-inform") == 4
    assert sample(registry, "kea_dhcp6_packets_received_total", server=SERVER, operation="addr-reg-reply") == 0


def test_dhcpv4_over_dhcpv6_packets(exporter, registry):
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP6,
        {"pkt6-dhcpv4-response-sent": stat(5), "pkt6-dhcpv4-query-received": stat(6)},
        {},
    )
    assert sample(registry, "kea_dhcp6_packets_sent_dhcp4_total", server=SERVER, operation="response") == 5
    assert sample(registry, "kea_dhcp6_packets_received_dhcp4_total", server=SERVER, operation="query") == 6


# ---------------------------------------------------------------- subnet and pool


def test_subnet_reading_leaves_the_pool_label_empty(exporter, registry):
    subnets = {1: subnet4(1, "192.168.1.0/24")}
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"subnet[1].assigned-addresses": stat(42)}, subnets)

    assert (
        sample(
            registry,
            "kea_dhcp4_addresses_assigned_total",
            server=SERVER,
            subnet="192.168.1.0/24",
            subnet_id="1",
            pool="",
        )
        == 42
    )


def test_pool_reading_carries_the_pool_name(exporter, registry):
    subnets = {1: subnet4(1, "192.168.1.0/24", pools=["192.168.1.10-192.168.1.100"])}
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"subnet[1].pool[0].assigned-addresses": stat(7)}, subnets)

    assert (
        sample(
            registry,
            "kea_dhcp4_addresses_assigned_total",
            server=SERVER,
            subnet="192.168.1.0/24",
            subnet_id="1",
            pool="192.168.1.10-192.168.1.100",
        )
        == 7
    )


def test_subnet_and_pool_readings_are_separate_series(exporter, registry):
    subnets = {1: subnet4(1, "192.168.1.0/24", pools=["192.168.1.10-192.168.1.100"])}
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP4,
        {"subnet[1].assigned-addresses": stat(9), "subnet[1].pool[0].assigned-addresses": stat(4)},
        subnets,
    )

    common = {"server": SERVER, "subnet": "192.168.1.0/24", "subnet_id": "1"}
    assert sample(registry, "kea_dhcp4_addresses_assigned_total", **common, pool="") == 9
    assert sample(registry, "kea_dhcp4_addresses_assigned_total", **common, pool="192.168.1.10-192.168.1.100") == 4


def test_allocation_failure_carries_its_context(exporter, registry):
    subnets = {1: subnet4(1, "192.168.1.0/24")}
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP4,
        {"subnet[1].v4-allocation-fail-no-pools": stat(2)},
        subnets,
    )

    assert (
        sample(
            registry,
            "kea_dhcp4_allocations_failed_total",
            server=SERVER,
            subnet="192.168.1.0/24",
            subnet_id="1",
            context="no-pools",
        )
        == 2
    )


def test_registered_nas_is_subnet_scoped(exporter, registry):
    """Kea reports registered-nas per subnet only, so the metric has no pool label."""
    subnets = {1: subnet6(1, "2001:db8::/64")}
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP6, {"subnet[1].registered-nas": stat(12)}, subnets)

    assert sample(registry, "kea_dhcp6_na_registered_total", server=SERVER, subnet="2001:db8::/64", subnet_id="1") == 12
    assert "pool" not in catalogue.labelnames(catalogue.entries_by_metric(DHCPVersion.DHCP6)["na_registered_total"])


def test_lease_reuses_are_subnet_scoped(exporter, registry):
    subnets = {1: subnet6(1, "2001:db8::/64")}
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP6,
        {"subnet[1].v6-ia-na-lease-reuses": stat(3), "subnet[1].v6-ia-pd-lease-reuses": stat(4)},
        subnets,
    )

    common = {"server": SERVER, "subnet": "2001:db8::/64", "subnet_id": "1"}
    assert sample(registry, "kea_dhcp6_na_reuses_total", **common) == 3
    assert sample(registry, "kea_dhcp6_pd_reuses_total", **common) == 4


# ---------------------------------------------------------------- DDNS


def test_ddns_global_statistics(exporter, registry):
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DDNS,
        {"ncr-received": stat(100), "update-sent": stat(90), "queue-mgr-queue-full": stat(1)},
        {},
    )
    assert sample(registry, "kea_ddns_ncr_received_total", server=SERVER) == 100
    assert sample(registry, "kea_ddns_update_sent_total", server=SERVER) == 90
    assert sample(registry, "kea_ddns_queue_full_total", server=SERVER) == 1


def test_ddns_per_key_statistics(exporter, registry):
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DDNS,
        {
            "key[example.com.].update-sent": stat(50),
            "key[example.com.].update-success": stat(45),
            "key[example.com.].update-error": stat(5),
            "key[test.org.].update-sent": stat(30),
        },
        {},
    )
    assert sample(registry, "kea_ddns_key_update_sent_total", server=SERVER, key="example.com.") == 50
    assert sample(registry, "kea_ddns_key_update_success_total", server=SERVER, key="example.com.") == 45
    assert sample(registry, "kea_ddns_key_update_error_total", server=SERVER, key="example.com.") == 5
    assert sample(registry, "kea_ddns_key_update_sent_total", server=SERVER, key="test.org.") == 30


def test_ddns_global_and_per_key_are_separate_metrics(exporter, registry):
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DDNS,
        {"update-sent": stat(80), "key[example.com.].update-sent": stat(20)},
        {},
    )
    assert sample(registry, "kea_ddns_update_sent_total", server=SERVER) == 80
    assert sample(registry, "kea_ddns_key_update_sent_total", server=SERVER, key="example.com.") == 20


def test_unknown_ddns_per_key_statistic_is_reported(exporter, capsys):
    exporter.parse_metrics(SERVER, DHCPVersion.DDNS, {"key[example.com.].invented": stat(10)}, {})
    assert "key[example.com.].invented" in capsys.readouterr().out


def test_ddns_statistic_without_a_per_key_metric_is_reported(exporter, capsys):
    """update-signed exists globally but Kea reports no per-key variant."""
    exporter.parse_metrics(SERVER, DHCPVersion.DDNS, {"key[example.com.].update-signed": stat(1)}, {})

    out = capsys.readouterr().out
    assert "update-signed" in out
    assert "ddns-key" in out


# ---------------------------------------------------------------- skipping


def test_never_exported_statistics_are_silent(exporter, registry, capsys):
    """Aggregates a finer statistic already covers, at every scope."""
    subnets = {1: subnet4(1, "192.168.1.0/24")}
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP4,
        {
            "cumulative-assigned-addresses": stat(1),
            "pkt4-sent": stat(2),
            "pkt4-received": stat(3),
            "v4-allocation-fail": stat(4),
            "subnet[1].cumulative-assigned-addresses": stat(5),
            "subnet[1].v4-allocation-fail": stat(6),
        },
        subnets,
    )
    assert capsys.readouterr().out == ""
    assert not samples(registry, "kea_dhcp4_addresses_assigned_total")


def test_cumulative_registered_nas_is_never_exported(exporter, capsys):
    subnets = {1: subnet6(1, "2001:db8::/64")}
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP6,
        {"cumulative-registered-nas": stat(1), "subnet[1].cumulative-registered-nas": stat(2)},
        subnets,
    )
    assert capsys.readouterr().out == ""


def test_unknown_dhcp_version_returns_early(exporter):
    exporter.parse_metrics(SERVER, "UNKNOWN", {"whatever": stat(1)}, {})


def test_malformed_statistic_value_is_skipped(exporter, capsys):
    """Kea reports [[value, timestamp]]; anything else is ignored."""
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP4,
        {"pkt4-ack-sent": [], "pkt4-nak-sent": None, "pkt4-offer-sent": 5},
        {},
    )
    assert capsys.readouterr().out == ""


def test_a_reading_that_is_not_a_value_and_timestamp_is_skipped(exporter, registry, capsys):
    """The outer list is checked; the reading inside it has to be checked too.

    Unpacking `value, _ = data[0]` raises on anything that is not a two-item
    sequence, which update() would report as a failure of the whole target.
    """
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP4,
        {
            "pkt4-ack-sent": [5],
            "pkt4-nak-sent": [{"value": 5}],
            "pkt4-offer-sent": stat(9),
        },
        {},
    )

    assert capsys.readouterr().out == ""
    assert sample(registry, "kea_dhcp4_packets_sent_total", server=SERVER, operation="offer") == 9


def test_a_subnet_without_a_prefix_exports_an_empty_label(exporter, registry):
    """A missing key must not reach Prometheus as the string "None"."""
    subnets = {1: {"id": 1, "pools": [{}]}}

    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"subnet[1].pool[0].assigned-addresses": stat(4)}, subnets)

    exposition = generate_latest(registry).decode()
    assert "None" not in exposition, exposition
    assert sample(registry, "kea_dhcp4_addresses_assigned_total", server=SERVER, subnet="", subnet_id="1", pool="") == 4


def test_vanished_subnet_is_reported_once(exporter, capsys):
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"subnet[9].assigned-addresses": stat(1)}, {})
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"subnet[9].assigned-addresses": stat(2)}, {})

    captured = capsys.readouterr()
    assert captured.err.count("subnet vanished") == 1
    assert "Ignoring metric because subnet vanished" in captured.err
    assert captured.out == ""


def test_vanished_pool_is_reported_once(exporter, capsys):
    subnets = {1: subnet4(1, "192.168.1.0/24", pools=["192.168.1.10-192.168.1.100"])}
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"subnet[1].pool[5].assigned-addresses": stat(1)}, subnets)
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"subnet[1].pool[5].assigned-addresses": stat(2)}, subnets)

    assert capsys.readouterr().err.count("subnet vanished") == 1


def test_vanished_pd_pool_is_reported(exporter, capsys):
    subnets = {1: subnet6(1, "2001:db8::/48", pd_pools=[("2001:db8:1::", 48, 64)])}
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP6, {"subnet[1].pd-pool[3].assigned-pds": stat(1)}, subnets)

    assert "subnet vanished" in capsys.readouterr().err


# ---------------------------------------------------------------- declared shape


def test_every_metric_carries_a_server_label():
    """The server label is what makes multi-target scrapes distinguishable."""
    for version in catalogue.CATALOGUE:
        for metric, entries in catalogue.entries_by_metric(version).items():
            assert "server" in catalogue.labelnames(entries), f"{version.name}:{metric}"


def test_dead_dhcp6_reservation_conflicts_metric_is_gone():
    """v6-reservation-conflicts is not a Kea statistic, so the metric was removed."""
    assert "reservation_conflicts_total" not in catalogue.METRICS[DHCPVersion.DHCP6]
    assert all(e.statistic != "v6-reservation-conflicts" for e in catalogue.CATALOGUE[DHCPVersion.DHCP6])
    assert all(e.statistic != "declined-reclaimed-addresses" for e in catalogue.CATALOGUE[DHCPVersion.DHCP6])
