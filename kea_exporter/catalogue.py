"""The catalogue: which Kea statistic becomes which Prometheus metric, and at what scope.

See CONTEXT.md for the vocabulary and docs/adr/0001 for why a statistic's
scope derives its metric's labels rather than the label sets being written by
hand.

Scope data is taken from the Kea Administrator Reference Manual and from the
StatsMgr::generateName call sites in the Kea source. Pool scope arrived in Kea
2.4.0 and has not been extended since.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from kea_exporter import DHCPVersion


class Scope(Enum):
    """The level a statistic is reported at."""

    GLOBAL = "global"
    SUBNET = "subnet"
    POOL = "pool"
    PD_POOL = "pd-pool"
    DDNS_KEY = "ddns-key"


SCOPE_LABELS: dict[Scope, tuple[str, ...]] = {
    Scope.GLOBAL: ("server",),
    Scope.SUBNET: ("server", "subnet", "subnet_id"),
    Scope.POOL: ("server", "subnet", "subnet_id", "pool"),
    Scope.PD_POOL: ("server", "subnet", "subnet_id", "pd_pool"),
    Scope.DDNS_KEY: ("server", "key"),
}

# Stable label order, so a metric's labels do not depend on set iteration.
LABEL_ORDER = ("server", "subnet", "subnet_id", "pool", "pd_pool", "key")

# Shorthands for the scope sets that actually occur.
_G = frozenset({Scope.GLOBAL})
_S = frozenset({Scope.SUBNET})
_SP = frozenset({Scope.SUBNET, Scope.POOL})
_SD = frozenset({Scope.SUBNET, Scope.PD_POOL})
_SPD = frozenset({Scope.SUBNET, Scope.POOL, Scope.PD_POOL})
_K = frozenset({Scope.DDNS_KEY})


@dataclass(frozen=True)
class Entry:
    """One statistic, and the metric it becomes.

    scopes lists the scopes the statistic is *exported* at. Kea may report it
    at others: a global aggregate of a subnet-scoped statistic is dropped
    without comment, because Kea emits those routinely.
    """

    statistic: str
    metric: str
    scopes: frozenset[Scope]
    labels: dict[str, str] = field(default_factory=dict)


def _packets(prefix: str, direction: str, operations: dict[str, str], metric: str) -> tuple[Entry, ...]:
    """Packet counters: one statistic per operation, all on one metric."""
    return tuple(
        Entry(f"{prefix}-{statistic}", metric, _G, {"operation": operation})
        for statistic, operation in operations.items()
    )


# --------------------------------------------------------------------------
# DHCPv4
# --------------------------------------------------------------------------

METRICS_DHCP4 = {
    "packets_sent_total": "Packets sent",
    "packets_received_total": "Packets received",
    "allocations_failed_total": "Allocation fail count",
    "addresses_assigned_total": "Assigned addresses",
    "addresses_declined_total": "Declined counts",
    "addresses_declined_reclaimed_total": "Declined addresses that were reclaimed",
    "addresses_reclaimed_total": "Expired addresses that were reclaimed",
    "addresses_total": "Size of subnet address pool",
    "reservation_conflicts_total": "Reservation conflict count",
    "leases_reused_total": "Number of times an IPv4 lease has been renewed in memory",
}

CATALOGUE_DHCP4 = (
    *_packets(
        "pkt4",
        "sent",
        {"ack-sent": "ack", "nak-sent": "nak", "offer-sent": "offer"},
        "packets_sent_total",
    ),
    # Kea 3.2 lease query responses.
    *_packets(
        "pkt4",
        "sent",
        {
            "lease-query-response-active-sent": "lease-query-response-active",
            "lease-query-response-unassigned-sent": "lease-query-response-unassigned",
            "lease-query-response-unknown-sent": "lease-query-response-unknown",
        },
        "packets_sent_total",
    ),
    *_packets(
        "pkt4",
        "received",
        {
            "discover-received": "discover",
            "offer-received": "offer",
            "request-received": "request",
            "ack-received": "ack",
            "nak-received": "nak",
            "release-received": "release",
            "decline-received": "decline",
            "inform-received": "inform",
            "unknown-received": "unknown",
            "parse-failed": "parse-failed",
            "receive-drop": "drop",
            # Kea 3.2 counters.
            "lease-query-received": "lease-query",
            "admin-filtered": "admin-filtered",
            "duplicate": "duplicate",
            "limit-exceeded": "limit-exceeded",
            "not-for-us": "not-for-us",
            "processing-failed": "processing-failed",
            "queue-full": "queue-full",
            "rfc-violation": "rfc-violation",
            "service-disabled": "service-disabled",
        },
        "packets_received_total",
    ),
    # Allocation failures: global and subnet, never pool.
    Entry("v4-allocation-fail-subnet", "allocations_failed_total", _S, {"context": "subnet"}),
    Entry("v4-allocation-fail-shared-network", "allocations_failed_total", _S, {"context": "shared-network"}),
    Entry("v4-allocation-fail-no-pools", "allocations_failed_total", _S, {"context": "no-pools"}),
    Entry("v4-allocation-fail-classes", "allocations_failed_total", _S, {"context": "classes"}),
    # Address counters: subnet and pool since Kea 2.4.0.
    Entry("assigned-addresses", "addresses_assigned_total", _SP),
    Entry("declined-addresses", "addresses_declined_total", _SP),
    Entry("reclaimed-declined-addresses", "addresses_declined_reclaimed_total", _SP),
    Entry("reclaimed-leases", "addresses_reclaimed_total", _SP),
    Entry("total-addresses", "addresses_total", _SP),
    # Subnet only: Kea reports no pool variant.
    Entry("v4-reservation-conflicts", "reservation_conflicts_total", _S),
    Entry("v4-lease-reuses", "leases_reused_total", _S),
)

# Statistics Kea reports that the exporter deliberately does not expose,
# because a finer-grained statistic already covers them.
NEVER_EXPORT_DHCP4 = frozenset(
    {
        "cumulative-assigned-addresses",
        "v4-allocation-fail",  # sum of the four suffixed variants
        "pkt4-sent",  # sum of the per-operation counters
        "pkt4-received",
    }
)

# --------------------------------------------------------------------------
# DHCPv6
# --------------------------------------------------------------------------

METRICS_DHCP6 = {
    "packets_sent_total": "Packets sent",
    "packets_received_total": "Packets received",
    "packets_sent_dhcp4_total": "DHCPv4-over-DHCPv6 Packets sent",
    "packets_received_dhcp4_total": "DHCPv4-over-DHCPv6 Packets received",
    "allocations_failed_total": "Allocation fail count",
    "addresses_declined_total": "Declined addresses",
    "addresses_declined_reclaimed_total": "Declined addresses that were reclaimed",
    "addresses_reclaimed_total": "Expired addresses that were reclaimed",
    "na_assigned_total": "Assigned non-temporary addresses (IA_NA)",
    "na_total": "Size of non-temporary address pool",
    "na_reuses_total": "Number of IA_NA lease reuses",
    "na_registered_total": "Registered non-temporary addresses via DHCPv6 address registration",
    "pd_assigned_total": "Assigned prefix delegations (IA_PD)",
    "pd_total": "Size of prefix delegation pool",
    "pd_reuses_total": "Number of IA_PD lease reuses",
}

CATALOGUE_DHCP6 = (
    *_packets(
        "pkt6",
        "sent",
        {
            "advertise-sent": "advertise",
            "reply-sent": "reply",
            # Address registration, Kea 3.0.0.
            "addr-reg-reply-sent": "addr-reg-reply",
            # Kea 3.2 lease query.
            "lease-query-reply-sent": "lease-query-reply",
        },
        "packets_sent_total",
    ),
    *_packets(
        "pkt6",
        "received",
        {
            "solicit-received": "solicit",
            "advertise-received": "advertise",
            "request-received": "request",
            "reply-received": "reply",
            "renew-received": "renew",
            "rebind-received": "rebind",
            "release-received": "release",
            "decline-received": "decline",
            "infrequest-received": "infrequest",
            "unknown-received": "unknown",
            "parse-failed": "parse-failed",
            "receive-drop": "drop",
            # Address registration, Kea 3.0.0. addr-reg-reply-received only
            # occurs when acting as a relay, but Kea initialises it to 0.
            "addr-reg-inform-received": "addr-reg-inform",
            "addr-reg-reply-received": "addr-reg-reply",
            # Kea 3.2 counters.
            "lease-query-received": "lease-query",
            "admin-filtered": "admin-filtered",
            "duplicate": "duplicate",
            "limit-exceeded": "limit-exceeded",
            "not-for-us": "not-for-us",
            "processing-failed": "processing-failed",
            "queue-full": "queue-full",
            "rfc-violation": "rfc-violation",
            "service-disabled": "service-disabled",
        },
        "packets_received_total",
    ),
    Entry("pkt6-dhcpv4-response-sent", "packets_sent_dhcp4_total", _G, {"operation": "response"}),
    Entry("pkt6-dhcpv4-query-received", "packets_received_dhcp4_total", _G, {"operation": "query"}),
    Entry("pkt6-dhcpv4-response-received", "packets_received_dhcp4_total", _G, {"operation": "response"}),
    Entry("v6-allocation-fail-subnet", "allocations_failed_total", _S, {"context": "subnet"}),
    Entry("v6-allocation-fail-shared-network", "allocations_failed_total", _S, {"context": "shared-network"}),
    Entry("v6-allocation-fail-no-pools", "allocations_failed_total", _S, {"context": "no-pools"}),
    Entry("v6-allocation-fail-classes", "allocations_failed_total", _S, {"context": "classes"}),
    # IA_NA: address pools.
    Entry("assigned-nas", "na_assigned_total", _SP),
    Entry("total-nas", "na_total", _SP),
    Entry("declined-addresses", "addresses_declined_total", _SP),
    Entry("reclaimed-declined-addresses", "addresses_declined_reclaimed_total", _SP),
    # IA_PD: prefix pools, never address pools.
    Entry("assigned-pds", "pd_assigned_total", _SD),
    Entry("total-pds", "pd_total", _SD),
    # The one statistic Kea reports under both pool kinds.
    Entry("reclaimed-leases", "addresses_reclaimed_total", _SPD),
    # Subnet only: Kea reports no pool variant.
    Entry("v6-ia-na-lease-reuses", "na_reuses_total", _S),
    Entry("v6-ia-pd-lease-reuses", "pd_reuses_total", _S),
    # Address registration, subnet scope only, Kea 3.0.0.
    Entry("registered-nas", "na_registered_total", _S),
)

NEVER_EXPORT_DHCP6 = frozenset(
    {
        "cumulative-assigned-addresses",
        "cumulative-assigned-nas",
        "cumulative-assigned-pds",
        "cumulative-registered-nas",
        "v6-allocation-fail",
        "pkt6-sent",
        "pkt6-received",
    }
)

# --------------------------------------------------------------------------
# DDNS
# --------------------------------------------------------------------------

METRICS_DDNS = {
    "ncr_error_total": "NCR processing errors",
    "ncr_invalid_total": "Invalid NCRs received",
    "ncr_received_total": "NCRs received",
    "queue_full_total": "Queue manager queue full",
    "update_error_total": "Update errors",
    "update_sent_total": "Updates sent",
    "update_signed_total": "Updates signed",
    "update_success_total": "Successful updates",
    "update_timeout_total": "Update timeouts",
    "update_unsigned_total": "Updates unsigned",
    "key_update_error_total": "Per-key update errors",
    "key_update_sent_total": "Per-key updates sent",
    "key_update_success_total": "Per-key successful updates",
    "key_update_timeout_total": "Per-key update timeouts",
}

CATALOGUE_DDNS = (
    Entry("ncr-error", "ncr_error_total", _G),
    Entry("ncr-invalid", "ncr_invalid_total", _G),
    Entry("ncr-received", "ncr_received_total", _G),
    Entry("queue-mgr-queue-full", "queue_full_total", _G),
    Entry("update-error", "update_error_total", _G),
    Entry("update-sent", "update_sent_total", _G),
    Entry("update-signed", "update_signed_total", _G),
    Entry("update-success", "update_success_total", _G),
    Entry("update-timeout", "update_timeout_total", _G),
    Entry("update-unsigned", "update_unsigned_total", _G),
    # The same statistic names again, reported per TSIG key.
    Entry("update-error", "key_update_error_total", _K),
    Entry("update-sent", "key_update_sent_total", _K),
    Entry("update-success", "key_update_success_total", _K),
    Entry("update-timeout", "key_update_timeout_total", _K),
)

NEVER_EXPORT_DDNS: frozenset[str] = frozenset()

# --------------------------------------------------------------------------

CATALOGUE = {
    DHCPVersion.DHCP4: CATALOGUE_DHCP4,
    DHCPVersion.DHCP6: CATALOGUE_DHCP6,
    DHCPVersion.DDNS: CATALOGUE_DDNS,
}

METRICS = {
    DHCPVersion.DHCP4: METRICS_DHCP4,
    DHCPVersion.DHCP6: METRICS_DHCP6,
    DHCPVersion.DDNS: METRICS_DDNS,
}

NEVER_EXPORT = {
    DHCPVersion.DHCP4: NEVER_EXPORT_DHCP4,
    DHCPVersion.DHCP6: NEVER_EXPORT_DHCP6,
    DHCPVersion.DDNS: NEVER_EXPORT_DDNS,
}

METRIC_PREFIX = {
    DHCPVersion.DHCP4: "kea_dhcp4",
    DHCPVersion.DHCP6: "kea_dhcp6",
    DHCPVersion.DDNS: "kea_ddns",
}


class CatalogueError(Exception):
    """The catalogue is internally inconsistent."""


def labelnames(entries) -> tuple[str, ...]:
    """The label set a metric carries, derived from the scopes of its entries."""
    names: set[str] = set()
    for entry in entries:
        for scope in entry.scopes:
            names.update(SCOPE_LABELS[scope])
        names.update(entry.labels)
    extra = sorted(names.difference(LABEL_ORDER))
    return tuple(name for name in LABEL_ORDER if name in names) + tuple(extra)


def group_by_metric(entries) -> dict[str, list[Entry]]:
    grouped: dict[str, list[Entry]] = {}
    for entry in entries:
        grouped.setdefault(entry.metric, []).append(entry)
    return grouped


def entries_by_metric(version: DHCPVersion) -> dict[str, list[Entry]]:
    return group_by_metric(CATALOGUE[version])


def index(version: DHCPVersion) -> dict[tuple[str, Scope], Entry]:
    """Look up an entry by the statistic name and the scope it was reported at."""
    lookup: dict[tuple[str, Scope], Entry] = {}
    for entry in CATALOGUE[version]:
        for scope in entry.scopes:
            lookup[(entry.statistic, scope)] = entry
    return lookup


def validate_daemon(name: str, entries, documentation, never) -> None:
    """Fail fast on a daemon's tables that cannot produce consistent metrics."""
    grouped = group_by_metric(entries)

    # index() keys on (statistic, scope): an entry with no scope gets no key, and
    # a repeated pair keeps only the last entry. Either loses a metric in silence.
    selectors = set()
    for entry in entries:
        if not entry.scopes:
            raise CatalogueError(f"{name}: entry {entry.statistic!r} declares no scopes, so nothing can reach it")
        for scope in entry.scopes:
            selector = (entry.statistic, scope)
            if selector in selectors:
                raise CatalogueError(f"{name}: duplicate statistic and scope: {selector[0]!r} at {scope.value!r}")
            selectors.add(selector)

    unknown = sorted({e.metric for e in entries}.difference(documentation))
    if unknown:
        raise CatalogueError(f"{name}: entries name metrics that are not declared: {unknown}")

    unused = sorted(set(documentation).difference(grouped))
    if unused:
        raise CatalogueError(f"{name}: metrics no entry maps to: {unused}")

    for metric, metric_entries in grouped.items():
        # Every entry for a metric must agree on the label set, or the first
        # statistic to arrive would fix labels the others cannot fill.
        sets = {labelnames([e]) for e in metric_entries}
        combined = set(labelnames(metric_entries))
        if any(set(one) != combined for one in sets):
            raise CatalogueError(f"{name}: entries for metric {metric!r} disagree on labels: {sorted(sets)}")

    for entry in entries:
        for label in entry.labels:
            if label in LABEL_ORDER:
                raise CatalogueError(f"{name}: entry {entry.statistic!r} sets {label!r}, which scope already provides")

    both = sorted({e.statistic for e in entries}.intersection(never))
    if both:
        raise CatalogueError(f"{name}: statistics both exported and never-exported: {both}")


def validate() -> None:
    """Fail fast on a catalogue that cannot produce consistent metrics."""
    for version in CATALOGUE:
        validate_daemon(version.name, CATALOGUE[version], METRICS[version], NEVER_EXPORT[version])


validate()
