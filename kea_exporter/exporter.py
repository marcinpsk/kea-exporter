import re
import sys
import time
from urllib.parse import urlparse

import click
from prometheus_client import Gauge

from kea_exporter import DHCPVersion
from kea_exporter.http import KeaHTTPClient
from kea_exporter.uds import KeaSocketClient


class Exporter:
    subnet_pattern = re.compile(
        r"^subnet\[(?P<subnet_id>[\d]+)\]\."
        r"(pool\[(?P<pool_index>[\d]+)\]\.(?P<pool_metric>[\w-]+)"
        r"|(?P<subnet_metric>[\w-]+))$"
    )

    def __init__(self, targets, stale_timeout: int = 0, registry=None, **kwargs) -> None:
        # prometheus
        """
        Initialize the Exporter: configure metric prefixes and metric
        containers, prepare DDNS/DHCP4/DHCP6 gauges and mappings,
        initialize tracking state for unhandled metrics and missing subnet
        info, and create client objects for each target.

        Parameters:
            targets (Iterable[str]): Iterable of target addresses. Each
                target is parsed as a URL; if it has a URL scheme a
                KeaHTTPClient is created, otherwise if it has a path a
                KeaSocketClient is created. Targets that cannot be parsed or
                that raise OSError during client creation are skipped and not
                added to self.targets.
            registry (CollectorRegistry): Prometheus registry to register
                metrics with. Defaults to the global REGISTRY.
            **kwargs: Additional keyword arguments forwarded to
                KeaHTTPClient or KeaSocketClient when creating clients.
        """
        from prometheus_client import REGISTRY

        self.registry = registry or REGISTRY
        self.prefix = "kea"
        self.prefix_dhcp4 = f"{self.prefix}_dhcp4"
        self.prefix_dhcp6 = f"{self.prefix}_dhcp6"
        self.prefix_ddns = f"{self.prefix}_ddns"

        self.metrics_dhcp4 = None
        self.metrics_dhcp4_map = None
        self.metrics_dhcp4_global_ignore = None
        self.metrics_dhcp4_subnet_ignore = None
        self.setup_dhcp4_metrics()

        self.metrics_dhcp6 = None
        self.metrics_dhcp6_map = None
        self.metrics_dhcp6_global_ignore = None
        self.metrics_dhcp6_subnet_ignore = None
        self.setup_dhcp6_metrics()

        self.metrics_ddns = None
        self.metrics_ddns_map = None
        self.ddns_key_pattern = None
        self.setup_ddns_metrics()

        # track unhandled metric keys, to notify only once
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
        self._last_success: dict[str, float] = {}

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
                # Log with the target URL stripped of credentials to avoid
                # leaking secrets in output (requests errors can embed the
                # original URL including embedded basic-auth credentials).
                safe_target = target
                parsed = urlparse(target)
                if parsed.username:
                    safe_host = parsed.hostname
                    if parsed.port:
                        safe_host = f"{safe_host}:{parsed.port}"
                    safe_target = f"{parsed.scheme}://{safe_host}{parsed.path}"
                click.echo(f"Failed to initialize target {safe_target}: {type(ex).__name__}: {ex}")
                # Keep placeholder so update() can retry initialization
                self.targets.append({"target": target, "client": None, "last_error": str(ex), "kwargs": kwargs})
                continue

            self.targets.append(client)

    def update(self):
        """
        Fetch metrics from all configured targets and update the
        exporter's Prometheus metrics.

        Iterates each configured client, retrieves that client's reported
        metric responses, and processes each response so the exporter's
        metric gauges reflect the latest values. Uninitialized targets
        (from failed client creation) are retried each update cycle.
        After all targets are processed, label combinations that existed in
        the previous cycle but not this one are removed from the registry —
        but only for servers that successfully responded this cycle, to avoid
        dropping valid metrics due to transient scrape failures.
        """
        self._seen_labels_current = {}
        successful_servers: set = set()

        for i, target in enumerate(self.targets):
            # Retry uninitialized targets
            if isinstance(target, dict) and target.get("client") is None:
                raw = target["target"]
                url = urlparse(raw)
                try:
                    if url.scheme:
                        client = KeaHTTPClient(raw, **target["kwargs"])
                    elif url.path:
                        client = KeaSocketClient(raw, **target["kwargs"])
                    else:
                        continue
                    self.targets[i] = client
                    target = client
                    click.echo(
                        f"Successfully initialized previously failed target: {getattr(client, '_server_id', raw)}"
                    )
                except Exception as ex:
                    target["last_error"] = str(ex)
                    continue

            try:
                for server_id, dhcp_version, arguments, subnets in target.stats():
                    self.parse_metrics(server_id, dhcp_version, arguments, subnets)
                    successful_servers.add(server_id)
                    self._last_success[server_id] = time.monotonic()
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
        for gauge_id, (gauge, prev_tuples) in self._seen_labels_previous.items():
            current_tuples = self._seen_labels_current.get(gauge_id, (None, set()))[1]
            stale = prev_tuples - current_tuples
            if not stale:
                continue
            label_names = list(gauge._labelnames)
            server_idx = label_names.index("server") if "server" in label_names else None
            for label_tuple in stale:
                server_id_val = label_tuple[server_idx] if server_idx is not None else None
                scraped_ok = server_id_val in successful_servers
                timed_out = (
                    self.stale_timeout > 0
                    and server_id_val is not None
                    and server_id_val in self._last_success
                    and (time.monotonic() - self._last_success[server_id_val]) > self.stale_timeout
                )
                if scraped_ok or timed_out or server_idx is None:
                    try:
                        gauge.remove(*label_tuple)
                    except Exception:
                        pass

        self._seen_labels_previous = self._seen_labels_current

    def setup_dhcp4_metrics(self):
        """
        Initialize Prometheus Gauge objects, mapping rules, and ignore
        lists for DHCPv4 metrics used by the exporter.

        Sets up the following attributes on self:
        - metrics_dhcp4: dictionary of Gauge objects for DHCPv4 (packet
          counters and per-subnet/pool metrics) with appropriate label
          sets (including the new "server" label).
        - metrics_dhcp4_map: mapping from KEA metric keys to internal
          metric names and any static labels required to populate Gauge
          labels.
        - metrics_dhcp4_global_ignore: list of KEA metric keys to ignore
          at the top (global) level.
        - metrics_dhcp4_subnet_ignore: list of KEA metric keys to ignore
          when processing subnet-level metrics.
        """
        self.metrics_dhcp4 = {
            # Packets
            "sent_packets": Gauge(
                f"{self.prefix_dhcp4}_packets_sent_total",
                "Packets sent",
                ["server", "operation"],
                registry=self.registry,
            ),
            "received_packets": Gauge(
                f"{self.prefix_dhcp4}_packets_received_total",
                "Packets received",
                ["server", "operation"],
                registry=self.registry,
            ),
            # per Subnet or Subnet pool
            "addresses_allocation_fail": Gauge(
                f"{self.prefix_dhcp4}_allocations_failed_total",
                "Allocation fail count",
                [
                    "server",
                    "subnet",
                    "subnet_id",
                    "context",
                ],
                registry=self.registry,
            ),
            "addresses_assigned_total": Gauge(
                f"{self.prefix_dhcp4}_addresses_assigned_total",
                "Assigned addresses",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            "addresses_declined_total": Gauge(
                f"{self.prefix_dhcp4}_addresses_declined_total",
                "Declined counts",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            "addresses_declined_reclaimed_total": Gauge(
                f"{self.prefix_dhcp4}_addresses_declined_reclaimed_total",
                "Declined addresses that were reclaimed",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            "addresses_reclaimed_total": Gauge(
                f"{self.prefix_dhcp4}_addresses_reclaimed_total",
                "Expired addresses that were reclaimed",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            "addresses_total": Gauge(
                f"{self.prefix_dhcp4}_addresses_total",
                "Size of subnet address pool",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            "reservation_conflicts_total": Gauge(
                f"{self.prefix_dhcp4}_reservation_conflicts_total",
                "Reservation conflict count",
                ["server", "subnet", "subnet_id"],
                registry=self.registry,
            ),
            "leases_reused_total": Gauge(
                f"{self.prefix_dhcp4}_leases_reused_total",
                "Number of times an IPv4 lease has been renewed in memory",
                ["server", "subnet", "subnet_id"],
                registry=self.registry,
            ),
        }

        self.metrics_dhcp4_map = {
            # sent_packets
            "pkt4-ack-sent": {
                "metric": "sent_packets",
                "labels": {"operation": "ack"},
            },
            "pkt4-nak-sent": {
                "metric": "sent_packets",
                "labels": {"operation": "nak"},
            },
            "pkt4-offer-sent": {
                "metric": "sent_packets",
                "labels": {"operation": "offer"},
            },
            # received_packets
            "pkt4-discover-received": {
                "metric": "received_packets",
                "labels": {"operation": "discover"},
            },
            "pkt4-offer-received": {
                "metric": "received_packets",
                "labels": {"operation": "offer"},
            },
            "pkt4-request-received": {
                "metric": "received_packets",
                "labels": {"operation": "request"},
            },
            "pkt4-ack-received": {
                "metric": "received_packets",
                "labels": {"operation": "ack"},
            },
            "pkt4-nak-received": {
                "metric": "received_packets",
                "labels": {"operation": "nak"},
            },
            "pkt4-release-received": {
                "metric": "received_packets",
                "labels": {"operation": "release"},
            },
            "pkt4-decline-received": {
                "metric": "received_packets",
                "labels": {"operation": "decline"},
            },
            "pkt4-inform-received": {
                "metric": "received_packets",
                "labels": {"operation": "inform"},
            },
            "pkt4-unknown-received": {
                "metric": "received_packets",
                "labels": {"operation": "unknown"},
            },
            "pkt4-parse-failed": {
                "metric": "received_packets",
                "labels": {"operation": "parse-failed"},
            },
            "pkt4-receive-drop": {
                "metric": "received_packets",
                "labels": {"operation": "drop"},
            },
            # per Subnet or pool
            "v4-allocation-fail-subnet": {
                "metric": "addresses_allocation_fail",
                "labels": {"context": "subnet"},
            },
            "v4-allocation-fail-shared-network": {
                "metric": "addresses_allocation_fail",
                "labels": {"context": "shared-network"},
            },
            "v4-allocation-fail-no-pools": {
                "metric": "addresses_allocation_fail",
                "labels": {"context": "no-pools"},
            },
            "v4-allocation-fail-classes": {
                "metric": "addresses_allocation_fail",
                "labels": {"context": "classes"},
            },
            "v4-lease-reuses": {
                "metric": "leases_reused_total",
            },
            "assigned-addresses": {
                "metric": "addresses_assigned_total",
            },
            "declined-addresses": {
                "metric": "addresses_declined_total",
            },
            "reclaimed-declined-addresses": {
                "metric": "addresses_declined_reclaimed_total",
            },
            "reclaimed-leases": {
                "metric": "addresses_reclaimed_total",
            },
            "total-addresses": {
                "metric": "addresses_total",
            },
            "v4-reservation-conflicts": {
                "metric": "reservation_conflicts_total",
            },
        }
        # Ignore list for Global level metrics
        self.metrics_dhcp4_global_ignore = [
            # metrics that exist at the subnet level in more detail
            "cumulative-assigned-addresses",
            "declined-addresses",
            # sums of different packet types
            "reclaimed-declined-addresses",
            "reclaimed-leases",
            "v4-reservation-conflicts",
            "v4-allocation-fail",
            "v4-allocation-fail-subnet",
            "v4-allocation-fail-shared-network",
            "v4-allocation-fail-no-pools",
            "v4-allocation-fail-classes",
            "pkt4-sent",
            "pkt4-received",
            "v4-lease-reuses",
        ]
        # Ignore list for subnet level metrics
        self.metrics_dhcp4_subnet_ignore = [
            "cumulative-assigned-addresses",
            "v4-allocation-fail",
        ]

    def setup_dhcp6_metrics(self):
        """
        Create and register Prometheus Gauges and the mappings and ignore
        lists used to export DHCPv6 metrics.

        Initializes these instance attributes:
        - metrics_dhcp6: named Gauge objects for DHCPv6 packet counts,
          DHCPv4-over-DHCPv6 counts, per-subnet/pool allocation and lease
          metrics, IA_NA and IA_PD metrics (with appropriate label sets).
        - metrics_dhcp6_map: mapping from KEA metric keys to entries that
          specify which Gauge to use and any static labels to apply.
        - metrics_dhcp6_global_ignore: list of KEA metric keys to ignore
          at the global (top) level.
        - metrics_dhcp6_subnet_ignore: list of KEA metric keys to ignore at
          the subnet level.
        """
        self.metrics_dhcp6 = {
            # Packets sent/received
            "sent_packets": Gauge(
                f"{self.prefix_dhcp6}_packets_sent_total",
                "Packets sent",
                ["server", "operation"],
                registry=self.registry,
            ),
            "received_packets": Gauge(
                f"{self.prefix_dhcp6}_packets_received_total",
                "Packets received",
                ["server", "operation"],
                registry=self.registry,
            ),
            # DHCPv4-over-DHCPv6
            "sent_dhcp4_packets": Gauge(
                f"{self.prefix_dhcp6}_packets_sent_dhcp4_total",
                "DHCPv4-over-DHCPv6 Packets sent",
                ["server", "operation"],
                registry=self.registry,
            ),
            "received_dhcp4_packets": Gauge(
                f"{self.prefix_dhcp6}_packets_received_dhcp4_total",
                "DHCPv4-over-DHCPv6 Packets received",
                ["server", "operation"],
                registry=self.registry,
            ),
            # per Subnet or pool
            "addresses_allocation_fail": Gauge(
                f"{self.prefix_dhcp6}_allocations_failed_total",
                "Allocation fail count",
                [
                    "server",
                    "subnet",
                    "subnet_id",
                    "context",
                ],
                registry=self.registry,
            ),
            "addresses_declined_total": Gauge(
                f"{self.prefix_dhcp6}_addresses_declined_total",
                "Declined addresses",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            "addresses_declined_reclaimed_total": Gauge(
                f"{self.prefix_dhcp6}_addresses_declined_reclaimed_total",
                "Declined addresses that were reclaimed",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            "addresses_reclaimed_total": Gauge(
                f"{self.prefix_dhcp6}_addresses_reclaimed_total",
                "Expired addresses that were reclaimed",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            "reservation_conflicts_total": Gauge(
                f"{self.prefix_dhcp6}_reservation_conflicts_total",
                "Reservation conflict count",
                ["server", "subnet", "subnet_id"],
                registry=self.registry,
            ),
            # IA_NA
            "na_assigned_total": Gauge(
                f"{self.prefix_dhcp6}_na_assigned_total",
                "Assigned non-temporary addresses (IA_NA)",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            "na_total": Gauge(
                f"{self.prefix_dhcp6}_na_total",
                "Size of non-temporary address pool",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            "na_reuses_total": Gauge(
                f"{self.prefix_dhcp6}_na_reuses_total",
                "Number of IA_NA lease reuses",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
            # IA_PD
            "pd_assigned_total": Gauge(
                f"{self.prefix_dhcp6}_pd_assigned_total",
                "Assigned prefix delegations (IA_PD)",
                ["server", "subnet", "subnet_id"],
                registry=self.registry,
            ),
            "pd_total": Gauge(
                f"{self.prefix_dhcp6}_pd_total",
                "Size of prefix delegation pool",
                ["server", "subnet", "subnet_id"],
                registry=self.registry,
            ),
            "pd_reuses_total": Gauge(
                f"{self.prefix_dhcp6}_pd_reuses_total",
                "Number of IA_PD lease reuses",
                ["server", "subnet", "subnet_id", "pool"],
                registry=self.registry,
            ),
        }

        self.metrics_dhcp6_map = {
            # sent_packets
            "pkt6-advertise-sent": {"metric": "sent_packets", "labels": {"operation": "advertise"}},
            "pkt6-reply-sent": {"metric": "sent_packets", "labels": {"operation": "reply"}},
            # received_packets
            "pkt6-receive-drop": {"metric": "received_packets", "labels": {"operation": "drop"}},
            "pkt6-parse-failed": {"metric": "received_packets", "labels": {"operation": "parse-failed"}},
            "pkt6-solicit-received": {"metric": "received_packets", "labels": {"operation": "solicit"}},
            "pkt6-advertise-received": {"metric": "received_packets", "labels": {"operation": "advertise"}},
            "pkt6-request-received": {"metric": "received_packets", "labels": {"operation": "request"}},
            "pkt6-reply-received": {"metric": "received_packets", "labels": {"operation": "reply"}},
            "pkt6-renew-received": {"metric": "received_packets", "labels": {"operation": "renew"}},
            "pkt6-rebind-received": {"metric": "received_packets", "labels": {"operation": "rebind"}},
            "pkt6-release-received": {"metric": "received_packets", "labels": {"operation": "release"}},
            "pkt6-decline-received": {"metric": "received_packets", "labels": {"operation": "decline"}},
            "pkt6-infrequest-received": {"metric": "received_packets", "labels": {"operation": "infrequest"}},
            "pkt6-unknown-received": {"metric": "received_packets", "labels": {"operation": "unknown"}},
            # DHCPv4-over-DHCPv6
            "pkt6-dhcpv4-response-sent": {"metric": "sent_dhcp4_packets", "labels": {"operation": "response"}},
            "pkt6-dhcpv4-query-received": {"metric": "received_dhcp4_packets", "labels": {"operation": "query"}},
            "pkt6-dhcpv4-response-received": {"metric": "received_dhcp4_packets", "labels": {"operation": "response"}},
            # per Subnet
            "v6-allocation-fail-shared-network": {
                "metric": "addresses_allocation_fail",
                "labels": {"context": "shared-network"},
            },
            "v6-allocation-fail-subnet": {"metric": "addresses_allocation_fail", "labels": {"context": "subnet"}},
            "v6-allocation-fail-no-pools": {"metric": "addresses_allocation_fail", "labels": {"context": "no-pools"}},
            "v6-allocation-fail-classes": {"metric": "addresses_allocation_fail", "labels": {"context": "classes"}},
            "assigned-nas": {"metric": "na_assigned_total"},
            "assigned-pds": {"metric": "pd_assigned_total"},
            "declined-addresses": {"metric": "addresses_declined_total"},
            "declined-reclaimed-addresses": {"metric": "addresses_declined_reclaimed_total"},
            "reclaimed-declined-addresses": {"metric": "addresses_declined_reclaimed_total"},
            "reclaimed-leases": {"metric": "addresses_reclaimed_total"},
            "total-nas": {"metric": "na_total"},
            "total-pds": {"metric": "pd_total"},
            "v6-reservation-conflicts": {"metric": "reservation_conflicts_total"},
            "v6-ia-na-lease-reuses": {"metric": "na_reuses_total"},
            "v6-ia-pd-lease-reuses": {"metric": "pd_reuses_total"},
        }

        # Ignore list for Global level metrics
        self.metrics_dhcp6_global_ignore = [
            # metrics that exist at the subnet level in more detail
            "cumulative-assigned-addresses",
            "declined-addresses",
            # sums of different packet types
            "cumulative-assigned-nas",
            "cumulative-assigned-pds",
            "reclaimed-declined-addresses",
            "reclaimed-leases",
            "v6-reservation-conflicts",
            "v6-allocation-fail",
            "v6-allocation-fail-subnet",
            "v6-allocation-fail-shared-network",
            "v6-allocation-fail-no-pools",
            "v6-allocation-fail-classes",
            "v6-ia-na-lease-reuses",
            "v6-ia-pd-lease-reuses",
            "pkt6-sent",
            "pkt6-received",
        ]
        # Ignore list for subnet level metrics
        self.metrics_dhcp6_subnet_ignore = [
            "cumulative-assigned-addresses",
            "cumulative-assigned-nas",
            "cumulative-assigned-pds",
            "v6-allocation-fail",
        ]

    def setup_ddns_metrics(self):
        """
        Initialize DDNS-related Prometheus metrics, the mapping from
        external DDNS metric names to those metrics, and the per-key
        parsing pattern.

        Creates the following attributes on the instance:
        - metrics_ddns: dictionary of Prometheus Gauge objects for global
          DDNS counters (labeled by `server`) and per-key counters
          (labeled by `server` and `key`).
        - metrics_ddns_map: mapping from external DDNS metric identifiers
          to entries in `metrics_ddns`.
        - ddns_key_pattern: compiled regex that extracts the key and metric
          name from strings of the form `key[<key>].<metric>`.
        """
        self.metrics_ddns = {
            # Global DDNS metrics
            "ncr_error": Gauge(
                f"{self.prefix_ddns}_ncr_error_total", "NCR processing errors", ["server"], registry=self.registry
            ),
            "ncr_invalid": Gauge(
                f"{self.prefix_ddns}_ncr_invalid_total", "Invalid NCRs received", ["server"], registry=self.registry
            ),
            "ncr_received": Gauge(
                f"{self.prefix_ddns}_ncr_received_total", "NCRs received", ["server"], registry=self.registry
            ),
            "queue_full": Gauge(
                f"{self.prefix_ddns}_queue_full_total", "Queue manager queue full", ["server"], registry=self.registry
            ),
            "update_error": Gauge(
                f"{self.prefix_ddns}_update_error_total", "Update errors", ["server"], registry=self.registry
            ),
            "update_sent": Gauge(
                f"{self.prefix_ddns}_update_sent_total", "Updates sent", ["server"], registry=self.registry
            ),
            "update_signed": Gauge(
                f"{self.prefix_ddns}_update_signed_total", "Updates signed", ["server"], registry=self.registry
            ),
            "update_success": Gauge(
                f"{self.prefix_ddns}_update_success_total", "Successful updates", ["server"], registry=self.registry
            ),
            "update_timeout": Gauge(
                f"{self.prefix_ddns}_update_timeout_total", "Update timeouts", ["server"], registry=self.registry
            ),
            "update_unsigned": Gauge(
                f"{self.prefix_ddns}_update_unsigned_total", "Updates unsigned", ["server"], registry=self.registry
            ),
            # Per-key metrics
            "key_update_error": Gauge(
                f"{self.prefix_ddns}_key_update_error_total",
                "Per-key update errors",
                ["server", "key"],
                registry=self.registry,
            ),
            "key_update_sent": Gauge(
                f"{self.prefix_ddns}_key_update_sent_total",
                "Per-key updates sent",
                ["server", "key"],
                registry=self.registry,
            ),
            "key_update_success": Gauge(
                f"{self.prefix_ddns}_key_update_success_total",
                "Per-key successful updates",
                ["server", "key"],
                registry=self.registry,
            ),
            "key_update_timeout": Gauge(
                f"{self.prefix_ddns}_key_update_timeout_total",
                "Per-key update timeouts",
                ["server", "key"],
                registry=self.registry,
            ),
        }

        self.metrics_ddns_map = {
            "ncr-error": {"metric": "ncr_error"},
            "ncr-invalid": {"metric": "ncr_invalid"},
            "ncr-received": {"metric": "ncr_received"},
            "queue-mgr-queue-full": {"metric": "queue_full"},
            "update-error": {"metric": "update_error"},
            "update-sent": {"metric": "update_sent"},
            "update-signed": {"metric": "update_signed"},
            "update-success": {"metric": "update_success"},
            "update-timeout": {"metric": "update_timeout"},
            "update-unsigned": {"metric": "update_unsigned"},
        }

        self.ddns_per_key_map = {
            "update-error": "key_update_error",
            "update-sent": "key_update_sent",
            "update-success": "key_update_success",
            "update-timeout": "key_update_timeout",
        }

        # Pattern to match per-key metrics: key[domain.name.].metric-name
        self.ddns_key_pattern = re.compile(r"^key\[(?P<key>[^\]]+)\]\.(?P<metric>.+)$")

    def _resolve_subnet_labels(self, key, subnet_match, server_id, dhcp_version, subnets, subnet_ignore, labels):
        """Resolve subnet/pool context from a subnet pattern match.

        Returns (resolved_key, labels) on success, or None to skip this metric.
        """
        subnet_id = int(subnet_match.group("subnet_id"))
        pool_index = subnet_match.group("pool_index")
        pool_metric = subnet_match.group("pool_metric")
        subnet_metric = subnet_match.group("subnet_metric")

        if pool_metric in subnet_ignore or subnet_metric in subnet_ignore:
            return None

        subnet_data = subnets.get(subnet_id, [])
        if not subnet_data:
            cache_key = (server_id, dhcp_version)
            missing_info = self.subnet_missing_info_sent.setdefault(cache_key, set())
            if subnet_id not in missing_info:
                missing_info.add(subnet_id)
                click.echo(
                    f"Ignoring metric because subnet vanished from configuration: {dhcp_version.name=}, {subnet_id=}",
                    file=sys.stderr,
                )
            return None

        labels["subnet"] = subnet_data.get("subnet")
        labels["subnet_id"] = subnet_id

        if pool_index:
            pool_index = int(pool_index)
            subnet_pools = [pool.get("pool") for pool in subnet_data.get("pools", [])]

            if len(subnet_pools) <= pool_index:
                cache_key = (server_id, dhcp_version)
                missing_info = self.subnet_missing_info_sent.setdefault(cache_key, set())
                missing_key = f"{subnet_id}-{pool_index}"
                if missing_key not in missing_info:
                    missing_info.add(missing_key)
                    click.echo(
                        "Ignoring metric because subnet vanished from "
                        f"configuration: {dhcp_version.name=}, "
                        f"{subnet_id=}, {pool_index=}",
                        file=sys.stderr,
                    )
                return None
            return pool_metric, labels | {"pool": subnet_pools[pool_index]}
        else:
            return subnet_metric, labels | {"pool": ""}

    def _handle_ddns_per_key(self, key, value, labels):
        """Handle DDNS per-key metrics. Returns True if handled, False otherwise."""
        key_match = self.ddns_key_pattern.match(key)
        if not key_match:
            return False

        key_name = key_match.group("key")
        metric_name = key_match.group("metric")

        metric_key = self.ddns_per_key_map.get(metric_name)
        if metric_key is None:
            self._report_unhandled(
                key,
                f"Unhandled DDNS per-key metric '{key}' "
                "please file an issue at https://github.com/marcinpsk/kea-exporter",
            )
            return True

        metric = self.metrics_ddns[metric_key]
        labels["key"] = key_name
        self._set_metric(metric, labels, value)
        return True

    def _set_metric(self, metric, labels, value):
        """Filter labels to those configured on the metric and set the value."""
        # _labelnames is a private attribute of prometheus_client.Gauge but
        # there is no public accessor; access is centralised here.
        filtered = {k: v for k, v in labels.items() if k in metric._labelnames}
        metric.labels(**filtered).set(value)
        # Record this label combination so stale entries can be pruned later.
        gauge_id = id(metric)
        if gauge_id not in self._seen_labels_current:
            self._seen_labels_current[gauge_id] = (metric, set())
        label_tuple = tuple(str(filtered.get(k, "")) for k in metric._labelnames)
        self._seen_labels_current[gauge_id][1].add(label_tuple)

    def _report_unhandled(self, key, message):
        """Report an unhandled metric key once."""
        if key not in self.unhandled_metrics:
            click.echo(message)
            self.unhandled_metrics.add(key)

    def parse_metrics(self, server, dhcp_version, arguments, subnets):
        """Parse KEA metrics and export them as Prometheus Gauges."""
        if dhcp_version is DHCPVersion.DHCP4:
            metrics_map = self.metrics_dhcp4_map
            metrics = self.metrics_dhcp4
            global_ignore = self.metrics_dhcp4_global_ignore
            subnet_ignore = self.metrics_dhcp4_subnet_ignore
        elif dhcp_version is DHCPVersion.DHCP6:
            metrics_map = self.metrics_dhcp6_map
            metrics = self.metrics_dhcp6
            global_ignore = self.metrics_dhcp6_global_ignore
            subnet_ignore = self.metrics_dhcp6_subnet_ignore
        elif dhcp_version is DHCPVersion.DDNS:
            metrics_map = self.metrics_ddns_map
            metrics = self.metrics_ddns
            global_ignore = []
            subnet_ignore = []
        else:
            return

        for key, data in arguments.items():
            if key in global_ignore:
                continue

            if not isinstance(data, list) or not data:
                continue
            value, _ = data[0]
            labels = {"server": server}

            subnet_match = self.subnet_pattern.match(key)
            if subnet_match:
                result = self._resolve_subnet_labels(
                    key, subnet_match, server, dhcp_version, subnets, subnet_ignore, labels
                )
                if result is None:
                    continue
                key, labels = result

            if dhcp_version is DHCPVersion.DDNS and self._handle_ddns_per_key(key, value, labels):
                continue

            # Handle standard metrics
            metric_info = metrics_map.get(key)
            if metric_info is None:
                self._report_unhandled(
                    key, f"Unhandled metric '{key}' please file an issue at https://github.com/marcinpsk/kea-exporter"
                )
                continue

            metric = metrics[metric_info["metric"]]
            labels.update(metric_info.get("labels", {}))
            self._set_metric(metric, labels, value)
