"""
Tests for kea_exporter.exporter module
"""

import unittest
from unittest.mock import Mock, patch

from prometheus_client import CollectorRegistry

from kea_exporter import DHCPVersion, catalogue
from kea_exporter.exporter import Exporter


class TestExporterInit(unittest.TestCase):
    """Test Exporter initialization"""

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_init_builds_a_gauge_for_every_catalogued_metric(self, mock_http):
        """Every metric the catalogue declares is registered, for every daemon."""
        mock_http.return_value = Mock()

        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        for version, documentation in catalogue.METRICS.items():
            self.assertEqual(set(exporter.metrics[version]), set(documentation), version.name)
            for metric, gauge in exporter.metrics[version].items():
                entries = catalogue.entries_by_metric(version)[metric]
                self.assertEqual(gauge._labelnames, catalogue.labelnames(entries), f"{version.name}:{metric}")

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_init_http_target(self, mock_http):
        """Test initialization with HTTP target"""
        mock_client = Mock()
        mock_http.return_value = mock_client

        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        self.assertEqual(len(exporter.targets), 1)
        self.assertEqual(exporter.targets[0], mock_client)
        mock_http.assert_called_once()

    @patch("kea_exporter.exporter.KeaSocketClient")
    def test_init_socket_target(self, mock_socket):
        """Test initialization with Unix socket target"""
        mock_client = Mock()
        mock_socket.return_value = mock_client

        exporter = Exporter(targets=["/var/run/kea/control.sock"], registry=self.registry)

        self.assertEqual(len(exporter.targets), 1)
        self.assertEqual(exporter.targets[0], mock_client)
        mock_socket.assert_called_once()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("kea_exporter.exporter.KeaSocketClient")
    def test_init_multiple_targets(self, mock_socket, mock_http):
        """Test initialization with multiple targets"""
        mock_http_client = Mock()
        mock_socket_client = Mock()
        mock_http.return_value = mock_http_client
        mock_socket.return_value = mock_socket_client

        exporter = Exporter(
            targets=["http://localhost:8000", "/var/run/kea/socket1", "http://remote:8001"], registry=self.registry
        )

        self.assertEqual(len(exporter.targets), 3)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_init_passes_timeout_to_http_client(self, mock_http):
        """Test that timeout is passed to HTTP client"""
        mock_http.return_value = Mock()

        Exporter(targets=["http://localhost:8000"], timeout=30, registry=self.registry)

        mock_http.assert_called_once()
        call_kwargs = mock_http.call_args[1]
        self.assertEqual(call_kwargs["timeout"], 30)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_init_handles_invalid_target(self, mock_echo, mock_http):
        """Test handling of invalid target format"""
        mock_http.side_effect = OSError("Connection failed")

        exporter = Exporter(targets=["http://invalid:8000"], registry=self.registry)

        # Should have echoed error but not crashed
        mock_echo.assert_called()
        # Failed targets are kept as placeholder dicts for retry
        self.assertEqual(len(exporter.targets), 1)
        self.assertIsInstance(exporter.targets[0], dict)
        self.assertIsNone(exporter.targets[0]["client"])

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_init_strips_credentials_from_error(self, mock_echo, mock_http):
        """Test that credentials are not leaked in error messages"""
        mock_http.side_effect = ConnectionError("refused")

        exporter = Exporter(targets=["http://admin:s3cret@kea.local:8000/api"], registry=self.registry)

        # Failed targets are kept as placeholder dicts for retry
        self.assertEqual(len(exporter.targets), 1)
        self.assertIsInstance(exporter.targets[0], dict)
        error_msg = mock_echo.call_args[0][0]
        self.assertNotIn("admin", error_msg)
        self.assertNotIn("s3cret", error_msg)
        self.assertIn("kea.local:8000", error_msg)


class TestExporterUpdate(unittest.TestCase):
    """Test Exporter.update method"""

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_update_calls_stats_on_all_targets(self, mock_http):
        """Test that update calls stats on all targets"""
        mock_client1 = Mock()
        mock_client1.stats.return_value = [("http://server1:8000", DHCPVersion.DHCP4, {}, {})]

        mock_client2 = Mock()
        mock_client2.stats.return_value = [("http://server2:8000", DHCPVersion.DHCP6, {}, {})]

        mock_http.side_effect = [mock_client1, mock_client2]

        exporter = Exporter(targets=["http://server1:8000", "http://server2:8000"], registry=self.registry)
        exporter.update()

        # Both clients should have had stats() called
        mock_client1.stats.assert_called_once()
        mock_client2.stats.assert_called_once()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_update_calls_parse_metrics(self, mock_http):
        """Test that update calls parse_metrics for each stats result"""
        mock_client = Mock()
        mock_client.stats.return_value = [
            ("http://server:8000", DHCPVersion.DHCP4, {"pkt4-received": [[100, "2024-01-01"]]}, {})
        ]

        mock_http.return_value = mock_client

        exporter = Exporter(targets=["http://server:8000"], registry=self.registry)

        with patch.object(exporter, "parse_metrics") as mock_parse:
            exporter.update()

            mock_parse.assert_called_once()
            call_args = mock_parse.call_args[0]
            self.assertEqual(call_args[0], "http://server:8000")  # server_id
            self.assertEqual(call_args[1], DHCPVersion.DHCP4)  # dhcp_version

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_update_continues_on_target_failure(self, mock_echo, mock_http):
        """Test that a failing target does not prevent other targets from being collected"""
        mock_client1 = Mock()
        mock_client1.stats.side_effect = ConnectionError("server1 down")
        mock_client1._server_id = "http://server1:8000"

        mock_client2 = Mock()
        mock_client2.stats.return_value = [("http://server2:8000", DHCPVersion.DHCP4, {}, {})]

        mock_http.side_effect = [mock_client1, mock_client2]

        exporter = Exporter(targets=["http://server1:8000", "http://server2:8000"], registry=self.registry)
        exporter.update()

        # First target failed but second should still have been called
        mock_client1.stats.assert_called_once()
        mock_client2.stats.assert_called_once()
        # Error should have been logged
        mock_echo.assert_called()
        error_msg = mock_echo.call_args[0][0]
        self.assertIn("server1", error_msg)


class TestStalePoolCleanup(unittest.TestCase):
    """Stale label removal after pool rename or deletion."""

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_renamed_pool_label_removed_on_next_scrape(self, mock_http):
        """Old pool label combo is pruned after the pool range is renamed in Kea."""
        from prometheus_client import generate_latest

        server = "http://kea-dhcp4:53100"
        subnet_id = 2
        subnet = "172.30.150.0/24"
        old_pool = "172.30.150.209-172.30.150.250"
        new_pool = "172.30.150.209-172.30.150.210"

        subnets_old = {subnet_id: {"subnet": subnet, "pools": [{"pool": old_pool}]}}
        args_old = {f"subnet[{subnet_id}].pool[0].assigned-addresses": [[5, "2024-01-01"]]}

        subnets_new = {subnet_id: {"subnet": subnet, "pools": [{"pool": new_pool}]}}
        args_new = {f"subnet[{subnet_id}].pool[0].assigned-addresses": [[3, "2024-01-01"]]}

        mock_client = Mock()
        mock_client.stats.side_effect = [
            iter([(server, DHCPVersion.DHCP4, args_old, subnets_old)]),
            iter([(server, DHCPVersion.DHCP4, args_new, subnets_new)]),
        ]
        mock_http.return_value = mock_client

        exporter = Exporter(targets=["http://kea-dhcp4:53100"], registry=self.registry)

        exporter.update()
        output = generate_latest(self.registry).decode()
        self.assertIn(old_pool, output)

        exporter.update()
        output = generate_latest(self.registry).decode()
        self.assertNotIn(old_pool, output)
        self.assertIn(new_pool, output)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_stale_label_not_pruned_when_server_scrape_fails(self, mock_http):
        """Labels from a server that failed to scrape are not pruned."""
        from prometheus_client import generate_latest

        server = "http://kea-dhcp4:53100"
        subnet_id = 1
        subnet = "10.0.0.0/24"
        pool = "10.0.0.100-10.0.0.200"

        subnets = {subnet_id: {"subnet": subnet, "pools": [{"pool": pool}]}}
        args = {f"subnet[{subnet_id}].pool[0].assigned-addresses": [[7, "2024-01-01"]]}

        mock_client = Mock()
        mock_client._server_id = server
        mock_client.stats.side_effect = [
            iter([(server, DHCPVersion.DHCP4, args, subnets)]),
            ConnectionError("target down"),
        ]
        mock_http.return_value = mock_client

        exporter = Exporter(targets=["http://kea-dhcp4:53100"], registry=self.registry)

        exporter.update()
        output = generate_latest(self.registry).decode()
        self.assertIn(pool, output)

        # Scrape fails — labels should be preserved, not pruned
        exporter.update()
        output = generate_latest(self.registry).decode()
        self.assertIn(pool, output)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_stale_label_pruned_after_timeout(self, mock_http):
        """Labels are pruned when stale_timeout expires after a scrape failure.

        Covers the full sequence: success → fail before timeout → fail after timeout.
        """
        from prometheus_client import generate_latest

        server = "http://kea-dhcp4:53100"
        subnet_id = 1
        subnet = "10.0.0.0/24"
        pool = "10.0.0.100-10.0.0.200"

        subnets = {subnet_id: {"subnet": subnet, "pools": [{"pool": pool}]}}
        args = {f"subnet[{subnet_id}].pool[0].assigned-addresses": [[7, "2024-01-01"]]}

        mock_client = Mock()
        mock_client._server_id = server
        mock_client.stats.side_effect = [
            iter([(server, DHCPVersion.DHCP4, args, subnets)]),
            ConnectionError("target down"),
            ConnectionError("target down"),
        ]
        mock_http.return_value = mock_client

        exporter = Exporter(targets=["http://kea-dhcp4:53100"], stale_timeout=60, registry=self.registry)

        with patch("kea_exporter.exporter.time.monotonic", return_value=0.0):
            exporter.update()

        output = generate_latest(self.registry).decode()
        self.assertIn(pool, output)

        # Scrape fails before timeout — label must still be present
        with patch("kea_exporter.exporter.time.monotonic", return_value=30.0):
            exporter.update()

        output = generate_latest(self.registry).decode()
        self.assertIn(pool, output)

        # Advance time past stale_timeout, then scrape fails — label must be pruned
        with patch("kea_exporter.exporter.time.monotonic", return_value=61.0):
            exporter.update()

        output = generate_latest(self.registry).decode()
        self.assertNotIn(pool, output)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_stale_label_not_pruned_when_timeout_disabled(self, mock_http):
        """Labels persist after repeated scrape failures when stale_timeout=0."""
        from prometheus_client import generate_latest

        server = "http://kea-dhcp4:53100"
        subnet_id = 1
        subnet = "10.0.0.0/24"
        pool = "10.0.0.100-10.0.0.200"

        subnets = {subnet_id: {"subnet": subnet, "pools": [{"pool": pool}]}}
        args = {f"subnet[{subnet_id}].pool[0].assigned-addresses": [[7, "2024-01-01"]]}

        mock_client = Mock()
        mock_client._server_id = server
        mock_client.stats.side_effect = [
            iter([(server, DHCPVersion.DHCP4, args, subnets)]),
            ConnectionError("target down"),
            ConnectionError("target down"),
        ]
        mock_http.return_value = mock_client

        exporter = Exporter(targets=["http://kea-dhcp4:53100"], stale_timeout=0, registry=self.registry)

        with patch("kea_exporter.exporter.time.monotonic", return_value=0.0):
            exporter.update()

        output = generate_latest(self.registry).decode()
        self.assertIn(pool, output)

        with patch("kea_exporter.exporter.time.monotonic", return_value=9999.0):
            exporter.update()
            exporter.update()

        output = generate_latest(self.registry).decode()
        self.assertIn(pool, output)

    def test_stale_timeout_zero_is_default(self):
        """Exporter.stale_timeout defaults to 0 when not specified."""
        with patch("kea_exporter.exporter.KeaHTTPClient"):
            exporter = Exporter(targets=["http://kea-dhcp4:53100"], registry=self.registry)
        self.assertEqual(exporter.stale_timeout, 0)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_dhcp6_labels_not_pruned_when_only_dhcp4_succeeds(self, mock_http):
        """dhcp6 labels are preserved when dhcp4 scrape succeeds but dhcp6 is absent."""
        from prometheus_client import generate_latest

        server = "http://kea:53100"
        subnet_id = 1
        subnet4 = "10.0.0.0/24"
        subnet6 = "2001:db8::/64"
        pool4 = "10.0.0.10-10.0.0.20"
        pool6 = "2001:db8::10-2001:db8::20"

        subnets4 = {subnet_id: {"subnet": subnet4, "pools": [{"pool": pool4}]}}
        subnets6 = {subnet_id: {"subnet": subnet6, "pools": [{"pool": pool6}]}}
        args4 = {f"subnet[{subnet_id}].pool[0].assigned-addresses": [[5, "2024-01-01"]]}
        args6 = {f"subnet[{subnet_id}].pool[0].assigned-nas": [[3, "2024-01-01"]]}

        mock_client = Mock()
        mock_client._server_id = server
        # First scrape: both dhcp4 and dhcp6 succeed
        # Second scrape: only dhcp4 succeeds (dhcp6 absent — simulates result!=0)
        mock_client.stats.side_effect = [
            iter(
                [
                    (server, DHCPVersion.DHCP4, args4, subnets4),
                    (server, DHCPVersion.DHCP6, args6, subnets6),
                ]
            ),
            iter(
                [
                    (server, DHCPVersion.DHCP4, args4, subnets4),
                    # dhcp6 absent
                ]
            ),
        ]
        mock_http.return_value = mock_client

        exporter = Exporter(targets=["http://kea:53100"], registry=self.registry)

        # First update: both modules scraped, both labels present
        exporter.update()
        output = generate_latest(self.registry).decode()
        self.assertIn(pool4, output)
        self.assertIn(pool6, output)

        # Second update: dhcp4 succeeds, dhcp6 absent — dhcp6 label MUST NOT be pruned
        exporter.update()
        output = generate_latest(self.registry).decode()
        self.assertIn(pool4, output)
        self.assertIn(pool6, output, "dhcp6 pool label was incorrectly pruned when only dhcp4 succeeded")


class TestRetryLimit(unittest.TestCase):
    """Test retry limit for failed target initialisation."""

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_give_up_after_max_retries(self, mock_echo, mock_http):
        """Target is skipped and a give-up message logged after MAX_TARGET_RETRIES failures."""
        from kea_exporter.exporter import MAX_TARGET_RETRIES

        mock_http.side_effect = OSError("Connection refused")
        exporter = Exporter(targets=["http://kea:8000"], registry=self.registry)

        # Target should be a placeholder dict
        self.assertIsInstance(exporter.targets[0], dict)

        mock_echo.reset_mock()

        # Exhaust all retries
        for _ in range(MAX_TARGET_RETRIES):
            exporter.update()

        # Give-up message must have been logged (on the final retry)
        logged_msgs = [call[0][0] for call in mock_echo.call_args_list]
        self.assertTrue(
            any("giving up" in msg.lower() for msg in logged_msgs),
            f"Expected give-up message; got: {logged_msgs}",
        )

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_no_init_attempt_after_max_retries(self, mock_echo, mock_http):
        """After MAX_TARGET_RETRIES the client constructor is no longer called."""
        from kea_exporter.exporter import MAX_TARGET_RETRIES

        mock_http.side_effect = OSError("Connection refused")
        exporter = Exporter(targets=["http://kea:8000"], registry=self.registry)

        # Exhaust all retries
        for _ in range(MAX_TARGET_RETRIES):
            exporter.update()

        # Reset to detect any further calls
        mock_http.reset_mock()
        mock_echo.reset_mock()

        exporter.update()

        # No further attempt to create the client
        mock_http.assert_not_called()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_successful_recovery_replaces_placeholder(self, mock_echo, mock_http):
        """After an initial failure, a successful re-init replaces the placeholder and stats() is called."""
        mock_client = Mock()
        mock_client.stats.return_value = iter([])
        # First call (during __init__) raises; second call (during update) succeeds
        mock_http.side_effect = [OSError("initial failure"), mock_client]

        exporter = Exporter(targets=["http://kea:8000"], registry=self.registry)

        # Target should be a placeholder dict after init failure
        self.assertIsInstance(exporter.targets[0], dict)

        # Next update() triggers _try_init_target which now succeeds
        exporter.update()

        # Placeholder should have been replaced with the real client
        self.assertIs(exporter.targets[0], mock_client)

        # stats() should have been called on the recovered client
        mock_client.stats.assert_called_once()


class TestGaugeRemoveExceptionHandling(unittest.TestCase):
    """Test narrowed exception handling in gauge.remove() during stale-label pruning."""

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_keyerror_from_gauge_remove_is_silenced(self, mock_http):
        """KeyError raised by gauge.remove() is silently ignored."""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)
        exporter.targets = []  # No live targets; only stale-label pruning runs

        mock_gauge = Mock()
        mock_gauge._labelnames = []  # No "server" label → server_idx is None → remove is always called
        mock_gauge.remove.side_effect = KeyError("label not found")

        exporter._seen_labels_previous = {id(mock_gauge): (mock_gauge, {()})}

        # Must not raise
        exporter.update()
        mock_gauge.remove.assert_called_once_with()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_unexpected_exception_from_gauge_remove_is_logged(self, mock_echo, mock_http):
        """A non-KeyError from gauge.remove() is logged to stderr."""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)
        exporter.targets = []

        mock_gauge = Mock()
        mock_gauge._labelnames = []  # no "server" label → server_idx=None → remove() called without server key lookup
        mock_gauge.remove.side_effect = ValueError("something went wrong")

        exporter._seen_labels_previous = {id(mock_gauge): (mock_gauge, {()})}

        # Must not raise
        exporter.update()

        # An error message must have been logged (with err=True)
        err_msgs = [call[0][0] for call in mock_echo.call_args_list if call[1].get("err")]
        self.assertTrue(
            any("Unexpected error removing gauge label" in m for m in err_msgs),
            f"Expected error log; got err= calls: {err_msgs}",
        )


class TestExporterKea32Statistics(unittest.TestCase):
    """Kea 3.2 expanded the statistics set; the exporter must stay resilient.

    - A global-scope aggregate (e.g. ``assigned-addresses``) that maps to a
      subnet-scoped gauge must not abort the whole scrape with
      ``ValueError('Incorrect label names')``.
    - The new 3.2 packet counters must be exported.
    """

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def _make_exporter(self, mock_http):
        mock_http.return_value = Mock()
        return Exporter(targets=["http://localhost:8000"], registry=self.registry)

    def test_global_assigned_addresses_does_not_abort_scrape_dhcp4(self):
        """A global assigned-addresses must not crash dhcp4 parsing; later stats still export."""
        exporter = self._make_exporter()
        server = "http://localhost:8000"
        arguments = {
            # global aggregate that maps to the subnet-scoped addresses_assigned_total
            # gauge — previously raised ValueError('Incorrect label names').
            "assigned-addresses": [[123, "2024-01-01 00:00:00"]],
            # a normal stat that must still be exported afterwards
            "pkt4-discover-received": [[7, "2024-01-01 00:00:00"]],
        }
        exporter.parse_metrics(server, DHCPVersion.DHCP4, arguments, subnets={})  # must not raise
        self.assertEqual(
            self.registry.get_sample_value(
                "kea_dhcp4_packets_received_total", {"server": server, "operation": "discover"}
            ),
            7,
        )
        # The redundant global aggregate was skipped, not exported to the subnet gauge.
        self.assertIsNone(
            self.registry.get_sample_value(
                "kea_dhcp4_addresses_assigned_total",
                {"server": server, "subnet": "", "subnet_id": "", "pool": ""},
            )
        )

    def test_global_assigned_nas_and_pds_do_not_abort_scrape_dhcp6(self):
        """Global assigned-nas / assigned-pds must not crash dhcp6 parsing."""
        exporter = self._make_exporter()
        server = "http://localhost:8000"
        arguments = {
            "assigned-nas": [[10, "t"]],
            "assigned-pds": [[5, "t"]],
            "pkt6-solicit-received": [[3, "t"]],
        }
        exporter.parse_metrics(server, DHCPVersion.DHCP6, arguments, subnets={})  # must not raise
        self.assertEqual(
            self.registry.get_sample_value(
                "kea_dhcp6_packets_received_total", {"server": server, "operation": "solicit"}
            ),
            3,
        )

    def test_new_pkt4_counters_exported(self):
        """The new Kea 3.2 pkt4-* counters map to packets_received/sent_total."""
        exporter = self._make_exporter()
        server = "http://localhost:8000"
        arguments = {
            "pkt4-lease-query-received": [[1, "t"]],
            "pkt4-lease-query-response-active-sent": [[2, "t"]],
            "pkt4-duplicate": [[3, "t"]],
            "pkt4-service-disabled": [[4, "t"]],
        }
        exporter.parse_metrics(server, DHCPVersion.DHCP4, arguments, subnets={})
        gsv = self.registry.get_sample_value
        self.assertEqual(gsv("kea_dhcp4_packets_received_total", {"server": server, "operation": "lease-query"}), 1)
        self.assertEqual(
            gsv("kea_dhcp4_packets_sent_total", {"server": server, "operation": "lease-query-response-active"}), 2
        )
        self.assertEqual(gsv("kea_dhcp4_packets_received_total", {"server": server, "operation": "duplicate"}), 3)
        self.assertEqual(
            gsv("kea_dhcp4_packets_received_total", {"server": server, "operation": "service-disabled"}), 4
        )

    def test_new_pkt6_counters_exported(self):
        """The new Kea 3.2 pkt6-* counters map to packets_received/sent_total."""
        exporter = self._make_exporter()
        server = "http://localhost:8000"
        arguments = {
            "pkt6-lease-query-received": [[1, "t"]],
            "pkt6-lease-query-reply-sent": [[2, "t"]],
            "pkt6-queue-full": [[5, "t"]],
        }
        exporter.parse_metrics(server, DHCPVersion.DHCP6, arguments, subnets={})
        gsv = self.registry.get_sample_value
        self.assertEqual(gsv("kea_dhcp6_packets_received_total", {"server": server, "operation": "lease-query"}), 1)
        self.assertEqual(gsv("kea_dhcp6_packets_sent_total", {"server": server, "operation": "lease-query-reply"}), 2)
        self.assertEqual(gsv("kea_dhcp6_packets_received_total", {"server": server, "operation": "queue-full"}), 5)


if __name__ == "__main__":
    unittest.main()
