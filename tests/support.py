"""Test doubles that let tests drive the real Exporter end to end.

Every test that uses these exercises real Gauge objects in a real
CollectorRegistry, so assertions read the exported series rather than a
mirrored copy of a gauge's label list.
"""

from kea_exporter.exporter import Exporter

TIMESTAMP = "2026-01-01 00:00:00.000000"


def stat(value):
    """Wrap a value in the shape Kea reports: a list of [value, timestamp] pairs."""
    return [[value, TIMESTAMP]]


def stats(**kwargs):
    """Build a Kea statistics payload from keyword pairs, dashes written as underscores."""
    return {key.replace("_", "-"): stat(value) for key, value in kwargs.items()}


class InMemoryTarget:
    """A Kea target that yields canned statistics instead of talking to a server.

    Satisfies the surface Exporter uses from a target: a server identifier and
    stats() yielding (server_id, dhcp_version, arguments, subnets).
    """

    def __init__(self, server_id="memory://kea"):
        # Exporter reads _server_id for error messages; see ADR-0001 follow-up.
        self._server_id = server_id
        self.rows = []
        self.calls = 0

    @property
    def server_id(self):
        return self._server_id

    def add(self, dhcp_version, arguments, subnets=None):
        self.rows.append((self._server_id, dhcp_version, arguments, subnets or {}))
        return self

    def stats(self):
        self.calls += 1
        yield from self.rows


class FailingTarget:
    """A target whose stats() raises, for the per-target error path."""

    def __init__(self, server_id="memory://down", error=None):
        self._server_id = server_id
        self.error = error or ConnectionError("target unreachable")

    @property
    def server_id(self):
        return self._server_id

    def stats(self):
        raise self.error
        yield  # pragma: no cover - makes stats() a generator


def exporter_with(registry, *targets, **kwargs):
    """Build an Exporter wired to the given targets, with no network at construction."""
    exporter = Exporter(targets=[], registry=registry, **kwargs)
    exporter.targets = list(targets)
    return exporter


def samples(registry, name):
    """Every exported sample of a metric, whatever its labels.

    Negative assertions must not name labels: get_sample_value matches on the
    complete label set, so a partial one returns None even when the series is
    there, and `assert ... is None` can never fail.
    """
    return [s for metric in registry.collect() for s in metric.samples if s.name == name]


def subnet4(subnet_id, cidr, pools=()):
    """A Kea DHCPv4 subnet configuration entry."""
    return {"id": subnet_id, "subnet": cidr, "pools": [{"pool": p} for p in pools]}


def subnet6(subnet_id, cidr, pools=(), pd_pools=()):
    """A Kea DHCPv6 subnet configuration entry.

    pd_pools entries are (prefix, prefix_len, delegated_len) triples.
    """
    return {
        "id": subnet_id,
        "subnet": cidr,
        "pools": [{"pool": p} for p in pools],
        "pd-pools": [
            {"prefix": prefix, "prefix-len": prefix_len, "delegated-len": delegated_len}
            for prefix, prefix_len, delegated_len in pd_pools
        ],
    }
