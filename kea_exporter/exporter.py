import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import click
from prometheus_client import Gauge

from kea_exporter import DHCPVersion, catalogue
from kea_exporter.catalogue import Scope
from kea_exporter.http import KeaHTTPClient
from kea_exporter.lifecycle import LabelLifecycle, Source
from kea_exporter.target import KeaTarget, SourceFailure
from kea_exporter.uds import KeaSocketClient

ISSUE_URL = "https://github.com/marcinpsk/kea-exporter"


class DuplicateTargetIdentityError(ValueError):
    """More than one Target would publish the same server label."""


def _quantity(count: int, singular: str, plural: str | None = None) -> str:
    """Format a count with the correct singular or plural noun."""
    noun = singular if count == 1 else plural or f"{singular}s"
    return f"{count} {noun}"


@dataclass(frozen=True)
class ScrapeReport:
    """Summary of one complete scrape cycle."""

    targets_total: int
    targets_reached: int
    sources_total: int
    sources_succeeded: int
    statistics_received: int
    label_combinations_updated: int
    stale_labels_removed: int
    elapsed_seconds: float

    def summary(self) -> str:
        """Return the concise operator-facing summary."""
        elapsed_ms = round(self.elapsed_seconds * 1000)
        target_noun = "target" if self.targets_total == 1 else "targets"
        source_noun = "source" if self.sources_total == 1 else "sources"
        return (
            f"Scrape complete: {self.targets_reached}/{self.targets_total} "
            f"{target_noun} reached, "
            f"{self.sources_succeeded}/{self.sources_total} {source_noun} succeeded, "
            f"{_quantity(self.statistics_received, 'statistic', 'statistics')} received, "
            f"{_quantity(self.label_combinations_updated, 'label combination')} updated, "
            f"{_quantity(self.stale_labels_removed, 'stale label')} removed in {elapsed_ms} ms"
        )


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
        self.unhandled_statistics = set()

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
        self._failing_sources: set[Source] = set()

        for target in targets:
            try:
                url = urlparse(target)
                if url.scheme:
                    self.targets.append(KeaHTTPClient(target, **kwargs))
                elif url.path:
                    self.targets.append(KeaSocketClient(target, **kwargs))
                else:
                    click.echo(f"Unable to parse target argument: {target}", err=True)
            except Exception as ex:
                click.echo(f"Failed to initialize target {_safe_target(target)}: {type(ex).__name__}: {ex}", err=True)

        seen_target_identities = set()
        duplicate_target_identities = []
        for target in self.targets:
            if target.server_id in seen_target_identities and target.server_id not in duplicate_target_identities:
                duplicate_target_identities.append(target.server_id)
            seen_target_identities.add(target.server_id)
        if duplicate_target_identities:
            raise DuplicateTargetIdentityError(
                "Target identity is configured more than once: " + ", ".join(duplicate_target_identities)
            )

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
        click.echo(f"Failed to collect statistics from {target.server_id}: {type(ex).__name__}: {ex}", err=True)

    def _report_target_recovery(self, target: KeaTarget) -> None:
        """Close the report opened by _report_target_failure."""
        if target.server_id not in self._failing_targets:
            return
        self._failing_targets.discard(target.server_id)
        click.echo(f"Collecting statistics from {target.server_id} again", err=True)

    def _report_source_failure(self, failure: SourceFailure) -> None:
        """Report a failing source once, not once per scrape while it stays down."""
        source = (failure.server_id, failure.daemon)
        if source in self._failing_sources:
            return
        self._failing_sources.add(source)
        click.echo(
            f"Failed to collect {failure.daemon.name} source from {failure.server_id}: "
            f"{type(failure.error).__name__}: {failure.error}",
            err=True,
        )

    def _report_source_recovery(self, source: Source) -> None:
        """Close the report opened by _report_source_failure."""
        if source not in self._failing_sources:
            return
        self._failing_sources.discard(source)
        server_id, daemon = source
        click.echo(f"Collecting {daemon.name} source from {server_id} again", err=True)

    def update(self) -> ScrapeReport:
        """
        Fetch statistics from all configured targets and update the metrics.

        Successful source results publish only after all successful rows from
        that target parse. After all targets finish, the label lifecycle
        processes stale labels. Returns a summary of the completed scrape
        cycle.
        """
        started_at = time.monotonic()
        scraped: set[Source] = set()
        targets_reached = 0
        sources_total = 0
        statistics_received = 0
        updated_label_combinations: set[tuple[int, tuple[str, ...]]] = set()

        for target in self.targets:
            try:
                # Materialise the generator before mutating any gauges.
                source_results = list(target.stats())
                targets_reached += 1
                sources_total += len(source_results)
                completed_sources: set[Source] = set()
                failed_sources = []
                target_statistics = 0
                pending_updates = []
                for result in source_results:
                    if isinstance(result, SourceFailure):
                        failed_sources.append(result)
                        continue
                    server_id, dhcp_version, arguments, subnets = result
                    target_statistics += len(arguments)
                    pending_updates.extend(self._parse_metric_updates(server_id, dhcp_version, arguments, subnets))
                    completed_sources.add((server_id, dhcp_version))
                target_label_combinations: set[tuple[int, tuple[str, ...]]] = set()
                for metric, labelnames, source, labels, value in pending_updates:
                    label_values = self._set_metric(metric, labelnames, labels, value)
                    self.lifecycle.record(metric, source, label_values)
                    target_label_combinations.add((id(metric), label_values))
                scraped.update(completed_sources)
                statistics_received += target_statistics
                updated_label_combinations.update(target_label_combinations)
                for failure in failed_sources:
                    self._report_source_failure(failure)
                for source in completed_sources:
                    self._report_source_recovery(source)
                self._report_target_recovery(target)
            except Exception as ex:
                self._report_target_failure(target, ex)

        stale_labels_removed = self.lifecycle.end_cycle(scraped, time.monotonic())
        return ScrapeReport(
            targets_total=len(self.targets),
            targets_reached=targets_reached,
            sources_total=sources_total,
            sources_succeeded=len(scraped),
            statistics_received=statistics_received,
            label_combinations_updated=len(updated_label_combinations),
            stale_labels_removed=stale_labels_removed,
            elapsed_seconds=time.monotonic() - started_at,
        )

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
            self._report_missing(
                server_id,
                dhcp_version,
                subnet_id,
                "subnet",
                f"{dhcp_version.name=}, {subnet_id=}",
            )
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
                pool_kind,
                f"{dhcp_version.name=}, {subnet_id=}, {pool_kind=}, {pool_index=}",
            )
            return None

        labels[label] = pools[pool_index]
        return scope, statistic, labels

    def _report_missing(self, server_id, dhcp_version, cache_entry, subject, detail):
        """Report a vanished subnet or pool once per server and daemon."""
        missing_info = self.subnet_missing_info_sent.setdefault((server_id, dhcp_version), set())
        if cache_entry in missing_info:
            return
        missing_info.add(cache_entry)
        click.echo(
            f"Ignoring statistic because {subject} vanished from configuration: {detail}",
            err=True,
        )

    def _set_metric(self, metric, labelnames, labels, value):
        """Set the value, filling any label the reading did not supply.

        A reading shallower than the metric's deepest scope leaves the deeper
        labels empty, so a subnet total and a pool total stay distinct series.
        """
        filtered = {name: str(labels.get(name, "")) for name in labelnames}
        metric.labels(**filtered).set(value)
        return tuple(filtered[name] for name in labelnames)

    def _report_unhandled(self, key, message):
        """Report an unhandled statistic once."""
        if key not in self.unhandled_statistics:
            click.echo(message, err=True)
            self.unhandled_statistics.add(key)

    def parse_metrics(self, server, dhcp_version, arguments, subnets):
        """Parse and export metrics without recording lifecycle labels.

        New label combinations are not eligible for lifecycle pruning. A label
        combination already tracked by update remains eligible for pruning.
        Use update for a complete scrape cycle.
        """
        for metric, labelnames, _source, labels, value in self._parse_metric_updates(
            server, dhcp_version, arguments, subnets
        ):
            self._set_metric(metric, labelnames, labels, value)

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
            try:
                value = float(reading[0])
            except (TypeError, ValueError):
                self._report_unhandled(
                    key,
                    f"Skipping statistic '{key}': value {reading[0]!r} is not a number",
                )
                continue

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
                self._report_unhandled(key, f"Unhandled statistic '{key}' please file an issue at {ISSUE_URL}")
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
    except ValueError:
        if "@" not in target:
            return target
        return "<unparsable target with credentials>"
