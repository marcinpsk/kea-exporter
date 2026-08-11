import re
import sys
import time
from urllib.parse import urlparse

import click
from prometheus_client import Gauge

from kea_exporter import DHCPVersion, catalogue
from kea_exporter.catalogue import Scope
from kea_exporter.http import KeaHTTPClient
from kea_exporter.uds import KeaSocketClient

MAX_TARGET_RETRIES = 10

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
                created. Targets that cannot be parsed are skipped; targets
                that raise during client creation keep a placeholder so
                update() can retry them.
            stale_timeout (int): Remove labels for a server silent longer than
                this many seconds. 0 disables the timeout.
            registry (CollectorRegistry): Registry to register metrics with.
                Defaults to the global REGISTRY.
            **kwargs: Forwarded to KeaHTTPClient or KeaSocketClient.
        """
        from prometheus_client import REGISTRY

        self.registry = registry or REGISTRY

        # metrics[version][metric_name] -> Gauge, built from the catalogue.
        self.metrics = {version: self._build_metrics(version) for version in catalogue.CATALOGUE}
        # index[version][(statistic, scope)] -> Entry
        self.index = {version: catalogue.index(version) for version in catalogue.CATALOGUE}
        # Statistics known at some scope, for telling a mis-scoped reading from
        # an unknown one.
        self.known = {
            version: {entry.statistic for entry in entries} for version, entries in catalogue.CATALOGUE.items()
        }

        # Maps id(gauge) -> DHCPVersion so the pruning loop can determine which
        # DHCP version a gauge belongs to without threading it through.
        self._gauge_to_dhcp_version: dict[int, DHCPVersion] = {}
        for version, gauges in self.metrics.items():
            for gauge in gauges.values():
                self._gauge_to_dhcp_version[id(gauge)] = version

        # track unhandled statistics, to notify only once
        self.unhandled_metrics = set()

        # track missing info per (server_id, dhcp_version), to notify only once
        self.subnet_missing_info_sent = {}

        # Track label combinations set in the current and previous scrape cycle.
        # Used to detect and remove stale metric children (e.g. renamed pools).
        self._seen_labels_current: dict = {}
        self._seen_labels_previous: dict = {}

        # Stale-label timeout: prune labels for servers silent longer than this.
        # 0 means disabled (default).
        self.stale_timeout = stale_timeout
        self._last_success: dict[tuple[str, DHCPVersion], float] = {}

        self.targets = []
        for target in targets:
            url = urlparse(target)
            client = None
            try:
                if url.scheme:
                    client = KeaHTTPClient(target, **kwargs)
                elif url.path:
                    client = KeaSocketClient(target, **kwargs)
                else:
                    click.echo(f"Unable to parse target argument: {target}")
                    continue
            except Exception as ex:
                click.echo(f"Failed to initialize target {_safe_target(target)}: {type(ex).__name__}: {ex}")
                # Keep placeholder so update() can retry initialization
                self.targets.append(
                    {"target": target, "client": None, "last_error": str(ex), "kwargs": kwargs, "retry_count": 0}
                )
                continue

            self.targets.append(client)

    def _build_metrics(self, version: DHCPVersion) -> dict:
        """Create one Gauge per metric the catalogue declares for this daemon."""
        prefix = catalogue.METRIC_PREFIX[version]
        documentation = catalogue.METRICS[version]
        return {
            metric: Gauge(
                f"{prefix}_{metric}",
                documentation[metric],
                catalogue.labelnames(entries),
                registry=self.registry,
            )
            for metric, entries in catalogue.entries_by_metric(version).items()
        }

    def _try_init_target(self, i: int, target: dict):
        """Attempt to re-initialize a failed target placeholder.

        Returns the newly created client on success, or None if the attempt
        was skipped (retry limit reached) or failed.
        """
        raw = target["target"]
        retry_count = target["retry_count"]
        if retry_count >= MAX_TARGET_RETRIES:
            return None
        url = urlparse(raw)
        try:
            if url.scheme:
                client = KeaHTTPClient(raw, **target["kwargs"])
            elif url.path:
                client = KeaSocketClient(raw, **target["kwargs"])
            else:
                return None
            self.targets[i] = client
            click.echo(f"Successfully initialized previously failed target: {getattr(client, '_server_id', raw)}")
            return client
        except Exception as ex:
            target["retry_count"] = retry_count + 1
            if target["retry_count"] >= MAX_TARGET_RETRIES:
                click.echo(
                    f"Target {_safe_target(raw)} failed to initialize after {MAX_TARGET_RETRIES} retries, giving up.",
                    err=True,
                )
            target["last_error"] = str(ex)
            return None

    def update(self):
        """
        Fetch statistics from all configured targets and update the metrics.

        Iterates each configured client, retrieves that client's reported
        statistics, and processes each response so the metrics reflect the
        latest values. Uninitialized targets (from failed client creation) are
        retried each update cycle. After all targets are processed, label
        combinations that existed in the previous cycle but not this one are
        removed from the registry, but only for servers that successfully
        responded this cycle, to avoid dropping valid metrics due to transient
        scrape failures.
        """
        self._seen_labels_current = {}
        successful_servers: set[tuple[str, DHCPVersion]] = set()

        for i, target in enumerate(self.targets):
            # Retry uninitialized targets
            if isinstance(target, dict) and target.get("client") is None:
                target = self._try_init_target(i, target)
                if target is None:
                    continue

            try:
                # Materialise the generator before mutating any gauges so that a
                # mid-stream exception cannot leave a partial snapshot in the registry
                # or seed _seen_labels_current with incomplete label tuples.
                stats_rows = list(target.stats())
                completed_server_versions: set[tuple[str, DHCPVersion]] = set()
                for server_id, dhcp_version, arguments, subnets in stats_rows:
                    self.parse_metrics(server_id, dhcp_version, arguments, subnets)
                    completed_server_versions.add((server_id, dhcp_version))
                scrape_finished_at = time.monotonic()
                for sv_pair in completed_server_versions:
                    successful_servers.add(sv_pair)
                    self._last_success[sv_pair] = scrape_finished_at
            except Exception as ex:
                click.echo(
                    f"Failed to collect metrics from {getattr(target, '_server_id', target)}: "
                    f"{type(ex).__name__}: {ex}",
                    err=True,
                )

        # Remove stale label combinations (e.g. a renamed pool) that were
        # present last cycle but absent this cycle.  Only do this for servers
        # that successfully delivered metrics this cycle; transient failures
        # should not cause valid metrics to be pruned.
        # Seed next_seen_labels from the current cycle; unpruned stale tuples
        # for silent servers are merged in below so they remain trackable.
        next_seen_labels: dict = {
            gauge_id: (gauge, set(current_tuples))
            for gauge_id, (gauge, current_tuples) in self._seen_labels_current.items()
        }

        for gauge_id, (gauge, prev_tuples) in self._seen_labels_previous.items():
            current_tuples = self._seen_labels_current.get(gauge_id, (None, set()))[1]
            stale = prev_tuples - current_tuples
            if not stale:
                continue
            label_names = list(gauge._labelnames)
            server_idx = label_names.index("server") if "server" in label_names else None
            gauge_dhcp_version = self._gauge_to_dhcp_version.get(gauge_id)
            for label_tuple in stale:
                server_id_val = label_tuple[server_idx] if server_idx is not None else None
                sv_pair = (
                    (server_id_val, gauge_dhcp_version)
                    if server_id_val is not None and gauge_dhcp_version is not None
                    else None
                )
                scraped_ok = sv_pair in successful_servers if sv_pair is not None else False
                timed_out = (
                    self.stale_timeout > 0
                    and sv_pair is not None
                    and sv_pair in self._last_success
                    and (time.monotonic() - self._last_success[sv_pair]) > self.stale_timeout
                )
                if scraped_ok or timed_out or server_idx is None:
                    try:
                        gauge.remove(*label_tuple)
                    except KeyError:
                        pass
                    except Exception as e:
                        click.echo(f"Unexpected error removing gauge label: {e}", err=True)
                else:
                    # Scrape failed and timeout not yet exceeded — keep the
                    # tuple in tracking so it can be pruned on a later cycle.
                    next_seen_labels.setdefault(gauge_id, (gauge, set()))[1].add(label_tuple)

        self._seen_labels_previous = next_seen_labels

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

    def _set_metric(self, metric, labels, value):
        """Set the value, filling any label the reading did not supply.

        A reading shallower than the metric's deepest scope leaves the deeper
        labels empty, so a subnet total and a pool total stay distinct series.
        """
        filtered = {name: str(labels.get(name, "")) for name in metric._labelnames}
        metric.labels(**filtered).set(value)
        # Record this label combination so stale entries can be pruned later.
        gauge_id = id(metric)
        if gauge_id not in self._seen_labels_current:
            self._seen_labels_current[gauge_id] = (metric, set())
        self._seen_labels_current[gauge_id][1].add(tuple(filtered[name] for name in metric._labelnames))

    def _report_unhandled(self, key, message):
        """Report an unhandled statistic once."""
        if key not in self.unhandled_metrics:
            click.echo(message)
            self.unhandled_metrics.add(key)

    def parse_metrics(self, server, dhcp_version, arguments, subnets):
        """Parse Kea statistics and export them as Prometheus metrics."""
        index = self.index.get(dhcp_version)
        if index is None:
            return
        metrics = self.metrics[dhcp_version]
        never_export = catalogue.NEVER_EXPORT[dhcp_version]
        known = self.known[dhcp_version]

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

            self._set_metric(metrics[entry.metric], {"server": server, **labels, **entry.labels}, value)


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
