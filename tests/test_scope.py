"""Scope behaviour: which statistic readings become series, and which are skipped.

See CONTEXT.md for the scope vocabulary and docs/adr/0001 for why a
statistic's scope derives its metric's labels.
"""

import pytest
from prometheus_client import CollectorRegistry

from kea_exporter import DHCPVersion
from tests.support import exporter_with, stat, subnet4, subnet6

SERVER = "memory://kea"


@pytest.fixture
def registry():
    return CollectorRegistry()


@pytest.fixture
def exporter(registry):
    return exporter_with(registry)


def test_pd_pool_statistics_are_exported(exporter, registry):
    """Prefix delegation pools are a scope of their own, not address pools."""
    subnets = {1: subnet6(1, "2001:db8::/48", pd_pools=[("2001:db8:1::", 48, 64)])}

    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP6,
        {
            "subnet[1].pd-pool[0].assigned-pds": stat(7),
            "subnet[1].pd-pool[0].total-pds": stat(9),
        },
        subnets,
    )

    labels = {
        "server": SERVER,
        "subnet": "2001:db8::/48",
        "subnet_id": "1",
        "pd_pool": "2001:db8:1::/48-64",
    }
    assert registry.get_sample_value("kea_dhcp6_pd_assigned_total", labels) == 7
    assert registry.get_sample_value("kea_dhcp6_pd_total", labels) == 9


def test_subnet_reading_of_pd_pool_statistic_uses_empty_label(exporter, registry):
    """A subnet total is still exported, with the deeper label left empty."""
    subnets = {1: subnet6(1, "2001:db8::/48", pd_pools=[("2001:db8:1::", 48, 64)])}

    exporter.parse_metrics(SERVER, DHCPVersion.DHCP6, {"subnet[1].assigned-pds": stat(4)}, subnets)

    labels = {"server": SERVER, "subnet": "2001:db8::/48", "subnet_id": "1", "pd_pool": ""}
    assert registry.get_sample_value("kea_dhcp6_pd_assigned_total", labels) == 4


def test_reclaimed_leases_distinguishes_pool_from_pd_pool(exporter, registry):
    """reclaimed-leases is the one statistic Kea reports under both pool kinds."""
    subnets = {
        1: subnet6(
            1,
            "2001:db8::/48",
            pools=["2001:db8::1-2001:db8::ffff"],
            pd_pools=[("2001:db8:1::", 48, 64)],
        )
    }

    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP6,
        {
            "subnet[1].pool[0].reclaimed-leases": stat(3),
            "subnet[1].pd-pool[0].reclaimed-leases": stat(4),
        },
        subnets,
    )

    common = {"server": SERVER, "subnet": "2001:db8::/48", "subnet_id": "1"}
    address_pool = registry.get_sample_value(
        "kea_dhcp6_addresses_reclaimed_total",
        {**common, "pool": "2001:db8::1-2001:db8::ffff", "pd_pool": ""},
    )
    prefix_pool = registry.get_sample_value(
        "kea_dhcp6_addresses_reclaimed_total",
        {**common, "pool": "", "pd_pool": "2001:db8:1::/48-64"},
    )
    assert (address_pool, prefix_pool) == (3, 4)


def test_pool_reading_of_subnet_scoped_statistic_is_skipped(exporter, registry, capsys):
    """Two pools must never collapse onto one subnet-scoped series.

    Kea has not reported v4-lease-reuses per pool since pool scope arrived in
    2.4.0. Before the catalogue, if it ever did, the second pool silently
    overwrote the first.
    """
    subnets = {1: subnet4(1, "10.0.0.0/24", pools=["10.0.0.10-10.0.0.99", "10.0.0.100-10.0.0.199"])}

    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP4,
        {
            "subnet[1].pool[0].v4-lease-reuses": stat(11),
            "subnet[1].pool[1].v4-lease-reuses": stat(22),
        },
        subnets,
    )

    labels = {"server": SERVER, "subnet": "10.0.0.0/24", "subnet_id": "1"}
    assert registry.get_sample_value("kea_dhcp4_leases_reused_total", labels) is None
    assert "v4-lease-reuses" in capsys.readouterr().out


def test_subnet_reading_of_subnet_scoped_statistic_still_exports(exporter, registry):
    """The skip above must not cost the legitimate subnet reading."""
    subnets = {1: subnet4(1, "10.0.0.0/24", pools=["10.0.0.10-10.0.0.99"])}

    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"subnet[1].v4-lease-reuses": stat(6)}, subnets)

    labels = {"server": SERVER, "subnet": "10.0.0.0/24", "subnet_id": "1"}
    assert registry.get_sample_value("kea_dhcp4_leases_reused_total", labels) == 6


def test_global_reading_of_subnet_scoped_statistic_is_silently_skipped(exporter, registry, capsys):
    """Kea emits global aggregates routinely, so they are dropped without noise.

    This replaces the hand-maintained per-daemon list of 22 statistic names.
    """
    exporter.parse_metrics(
        SERVER,
        DHCPVersion.DHCP4,
        {"total-addresses": stat(50), "assigned-addresses": stat(20)},
        {},
    )

    assert registry.get_sample_value("kea_dhcp4_addresses_total", {"server": SERVER}) is None
    assert capsys.readouterr().out == ""


def test_unknown_statistic_is_reported_once(exporter, capsys):
    """A statistic with no catalogue entry is still reported, once."""
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"pkt4-invented-received": stat(1)}, {})
    exporter.parse_metrics(SERVER, DHCPVersion.DHCP4, {"pkt4-invented-received": stat(2)}, {})

    assert capsys.readouterr().out.count("pkt4-invented-received") == 1
