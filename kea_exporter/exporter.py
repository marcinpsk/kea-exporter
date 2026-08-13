import re
import sys
import time
from urllib.parse import urlparse

import click
from prometheus_client import Gauge

from kea_exporter import DHCPVersion, catalogue
from kea_exporter.catalogue import Scope
from kea_exporter.http import KeaHTTPClient
from kea_exporter.lifecycle import LabelLifecycle, Source
from kea_exporter.target import KeaTarget
from kea_exporter.uds import KeaSocketClient

ISSUE_URL = "https://github.com/marcinpsk/kea-exporter"


class Exporter:
    # subnet[1].assigned-addresses, subnet[1].pool[0].assigned-addresses,
    # subnet[1].pd-pool[0].assigned-pds
    subnet_pattern = re.compile(
        r"^subnet\[(?P<subnet_id>\d+)\]\."
        r"(?:(?P<pool_kind>pd-pool|pool)\[(?P<pool_index>\d+)\]\.)?"
        r"(?P<statistic>[\w-]+)$"
    )
    # key[example.com.].update-sent
    ddns_key_pattern = re.compile(r"^key\[(?P<key>[^\]]+)\]\.(?P<statistic>.+)$")

    def __init__(self, targets, stale_timeout: int = 0, registry=None, **kwargs) -> None:
        """
        Initialize the Exporter: build the Prometheus metrics declared by the
        catalogue, prepare tracking state, and create a client for each target.

        Parameters:
            targets (Iterable[str]): Iterable of target addresses. Each target
                is parsed as a URL; if it has a URL scheme a KeaHTTPClient is
                created, otherwise if it has a path a KeaSocketClient is
                created. Construction failures are configuration errors, so
                the target is reported and dropped.
            stale_timeout (int): Remove labels for a server silent longer than
                this many seconds. 0 disables the timeout.
            registry (CollectorRegistry): Registry to register metrics with.
                Defaults to the global REGISTRY.
            **kwargs: Forwarded to KeaHTTPClient or KeaSocketClient.
        """
        from prometheus_client import REGISTRY

        self.registry = registry or REGISTRY

        # metrics[version][metric_name] -> Gauge, built from the catalogue.
        self.metrics = {}
        self.metric_labelnames = {}
        for version in catalogue.CATALOGUE:
            metrics, labelnames = self._build_metrics(version)
            self.metrics[version] = metrics
            self.metric_labelnames[version] = labelnames
        # index[version][(statistic, scope)] -> Entry
        self.index = {version: catalogue.index(version) for version in catalogue.CATALOGUE}
        # Statistics known at some scope, for telling a mis-scoped reading from
        # an unknown one.
        self.known = {
            version: {entry.statistic for entry in entries} for version, entries in catalogue.CATALOGUE.items()
        }

        # track unhandled statistics, to notify only once
        self.unhandled_metrics = set()

        # track missing info per (server_id, dhcp_version), to notify only once
        self.subnet_missing_info_sent = {}

        self.lifecycle = LabelLifecycle(stale_timeout)

        # Targets a scrape can be attempted against. Building one performs no
        # I/O, so anything that raises below is a configuration error that
        # retrying cannot fix, and that target is dropped rather than kept as a
        # placeholder to retry.
        self.targets: list[KeaTarget] = []
        # server_id of every target whose last scrape failed, so a target that
        # stays down is reported once rather than once per scrape.
        self._failing_targets: set[str] = set()

        for target in targets:
            url = urlparse(target)
            try:
                if url.scheme:
                    self.targets.append(KeaHTTPClient(target, **kwargs))
                elif url.path:
                    self.targets.append(KeaSocketClient(target, **kwargs))
                else:
                    click.echo(f"Unable to parse target argument: {target}", err=True)
            except Exception as ex:
                click.echo(f"Failed to initialize target {_safe_target(target)}: {type(ex).__name__}: {ex}", err=True)

    def _build_metrics(self, version: DHCPVersion) -> tuple[dict, dict]:
        """Create one Gauge per metric the catalogue declares for this daemon."""
        prefix = catalogue.METRIC_PREFIX[version]
        documentation = catalogue.METRICS[version]
        metrics = {}
        labelnames_by_metric = {}
        for metric, entries in catalogue.entries_by_metric(version).items():
            labelnames = catalogue.labelnames(entries)
            metrics[metric] = Gauge(
                f"{prefix}_{metric}",
                documentation[metric],
                labelnames,
                registry=self.registry,
            )
            labelnames_by_metric[metric] = labelnames
        return metrics, labelnames_by_metric

    def _report_target_failure(self, target: KeaTarget, ex: Exception) -> None:
        """Report a failing target once, not once per scrape while it stays down."""
        if target.server_id in self._failing_targets:
            return
        self._failing_targets.add(target.server_id)
        click.echo(f"Failed to collect metrics from {target.server_id}: {type(ex).__name__}: {ex}", err=True)

    def _report_target_recovery(self, target: KeaTarget) -> None:
        """Close the report opened by _report_target_failure."""
        if target.server_id not in self._failing_targets:
            return
        self._failing_targets.discard(target.server_id)
        click.echo(f"Collecting metrics from {target.server_id} again", err=True)

    def update(self):
        """
        Fetch statistics from all configured targets and update the metrics.

        A target counts as scraped only after all its rows parse successfully.
        After all targets finish, the label lifecycle processes stale labels.
        """
        scraped: set[Source] = set()

        for target in self.targets:
            try:
                # Materialise the generator before mutating any gauges.
                stats_rows = list(target.stats())
                completed_sources: set[Source] = set()
                pending_updates = []
                for server_id, dhcp_version, arguments, subnets in stats_rows:
                    pending_updates.extend(self._parse_metric_updates(server_id, dhcp_version, arguments, subnets))
                    completed_sources.add((server_id, dhcp_version))
                for update in pending_updates:
                    self._set_metric(*update)
                scraped.update(completed_sources)
                self._report_target_recovery(target)
            except Exception as ex:
                self._report_target_failure(target, ex)

        self.lifecycle.end_cycle(scraped, time.monotonic())

    def _resolve_selector(self, key, server_id, dhcp_version, subnets):
        """Split a statistic name into its scope, its bare name, and its labels.

        Returns (scope, statistic, labels), or None when the statistic names a
        subnet or pool that is no longer in the configuration.
        """
        if dhcp_version is DHCPVersion.DDNS:
            key_match = self.ddns_key_pattern.match(key)
            if key_match:
                return Scope.DDNS_KEY, key_match.group("statistic"), {"key": key_match.group("key")}
            return Scope.GLOBAL, key, {}

        subnet_match = self.subnet_pattern.match(key)
        if not subnet_match:
            return Scope.GLOBAL, key, {}

        subnet_id = int(subnet_match.group("subnet_id"))
        statistic = subnet_match.group("statistic")
        pool_kind = subnet_match.group("pool_kind")
        pool_index = subnet_match.group("pool_index")

        subnet_data = subnets.get(subnet_id, {})
        if not subnet_data:
            self._report_missing(server_id, dhcp_version, subnet_id, f"{dhcp_version.name=}, {subnet_id=}")
            return None

        # A missing key must not reach Prometheus as the string "None", which no
        # query could tell from a real value.
        labels = {"subnet": subnet_data.get("subnet") or "", "subnet_id": str(subnet_id)}
        if pool_kind is None:
            return Scope.SUBNET, statistic, labels

        pool_index = int(pool_index)
        if pool_kind == "pd-pool":
            pools = [_pd_pool_name(pool) for pool in subnet_data.get("pd-pools", [])]
            scope, label = Scope.PD_POOL, "pd_pool"
        else:
            pools = [pool.get("pool") or "" for pool in subnet_data.get("pools", [])]
            scope, label = Scope.POOL, "pool"

        if len(pools) <= pool_index:
            self._report_missing(
                server_id,
                dhcp_version,
                f"{subnet_id}-{pool_kind}-{pool_index}",
                f"{dhcp_version.name=}, {subnet_id=}, {pool_index=}",
            )
            return None

        labels[label] = pools[pool_index]
        return scope, statistic, labels

    def _report_missing(self, server_id, dhcp_version, cache_entry, detail):
        """Report a vanished subnet or pool once per server and daemon."""
        missing_info = self.subnet_missing_info_sent.setdefault((server_id, dhcp_version), set())
        if cache_entry in missing_info:
            return
        missing_info.add(cache_entry)
        click.echo(
            f"Ignoring metric because subnet vanished from configuration: {detail}",
            file=sys.stderr,
        )

    def _set_metric(self, metric, labelnames, source: Source, labels, value):
        """Set the value, filling any label the reading did not supply.

        A reading shallower than the metric's deepest scope leaves the deeper
        labels empty, so a subnet total and a pool total stay distinct series.
        """
        filtered = {name: str(labels.get(name, "")) for name in labelnames}
        metric.labels(**filtered).set(value)
        label_values = tuple(filtered[name] for name in labelnames)
        self.lifecycle.record(metric, source, label_values)

    def _report_unhandled(self, key, message):
        """Report an unhandled statistic once."""
        if key not in self.unhandled_metrics:
            click.echo(message)
            self.unhandled_metrics.add(key)

    def parse_metrics(self, server, dhcp_version, arguments, subnets):
        """Parse Kea statistics and export them as Prometheus metrics."""
        for update in self._parse_metric_updates(server, dhcp_version, arguments, subnets):
            self._set_metric(*update)

    def _parse_metric_updates(self, server, dhcp_version, arguments, subnets):
        """Parse Kea statistics without publishing them."""
        index = self.index.get(dhcp_version)
        if index is None:
            return []
        metrics = self.metrics[dhcp_version]
        never_export = catalogue.NEVER_EXPORT[dhcp_version]
        known = self.known[dhcp_version]
        updates = []

        for key, data in arguments.items():
            if not isinstance(data, list) or not data:
                continue
            # Kea reports [[value, timestamp]]. Unpacking anything else raises,
            # and update() would report that as a failure of the whole target.
            reading = data[0]
            if not isinstance(reading, (list, tuple)) or len(reading) != 2:
                continue
            value = reading[0]

            resolved = self._resolve_selector(key, server, dhcp_version, subnets)
            if resolved is None:
                continue
            scope, statistic, labels = resolved

            if statistic in never_export:
                continue

            entry = index.get((statistic, scope))
            if entry is None:
                if statistic in known:
                    # Kea emits global aggregates of subnet-scoped statistics
                    # routinely, so those are dropped without comment. Any
                    # other scope is a surprise worth reporting.
                    if scope is not Scope.GLOBAL:
                        self._report_unhandled(
                            key,
                            f"Skipping statistic '{key}': '{statistic}' is not exported at "
                            f"{scope.value} scope; please file an issue at {ISSUE_URL}",
                        )
                    continue
                self._report_unhandled(key, f"Unhandled metric '{key}' please file an issue at {ISSUE_URL}")
                continue

            updates.append(
                (
                    metrics[entry.metric],
                    self.metric_labelnames[dhcp_version][entry.metric],
                    (server, dhcp_version),
                    {"server": server, **labels, **entry.labels},
                    value,
                )
            )

        return updates


def _pd_pool_name(pool: dict) -> str:
    """Identify a prefix pool as prefix/prefix-len-delegated-len.

    Kea allows several pd-pools to share a prefix and differ only in the
    delegated length, so the delegated length is part of the identity.
    """
    return f"{pool.get('prefix')}/{pool.get('prefix-len')}-{pool.get('delegated-len')}"


def _safe_target(target: str) -> str:
    """Strip embedded credentials from a target, for logging."""
    try:
        parsed = urlparse(target)
        # A URL may carry a password with no username, where username is "".
        if not parsed.username and not parsed.password:
            return target
        # Cut the userinfo off the netloc; parsed.hostname would drop the
        # brackets from an IPv6 host. Same reconstruction as KeaHTTPClient.
        return f"{parsed.scheme}://{parsed.netloc.rpartition('@')[2]}{parsed.path}"
    except Exception:
        return target  # non-URL paths (UDS socket paths) pass through unchanged
