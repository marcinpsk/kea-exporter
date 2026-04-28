"""
Tests for kea_exporter.exporter module
"""

import unittest
from unittest.mock import Mock, patch

from prometheus_client import CollectorRegistry

from kea_exporter import DHCPVersion
from kea_exporter.exporter import Exporter


class TestExporterInit(unittest.TestCase):
    """Test Exporter initialization"""

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("kea_exporter.exporter.KeaSocketClient")
    def test_init_creates_metrics(self, mock_socket, mock_http):
        """Test that initialization creates all metric structures"""
        mock_http.return_value = Mock()

        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Check DHCP4 metrics setup
        self.assertIsNotNone(exporter.metrics_dhcp4)
        self.assertIsNotNone(exporter.metrics_dhcp4_map)
        self.assertIsNotNone(exporter.metrics_dhcp4_global_ignore)
        self.assertIsNotNone(exporter.metrics_dhcp4_subnet_ignore)

        # Check DHCP6 metrics setup
        self.assertIsNotNone(exporter.metrics_dhcp6)
        self.assertIsNotNone(exporter.metrics_dhcp6_map)
        self.assertIsNotNone(exporter.metrics_dhcp6_global_ignore)
        self.assertIsNotNone(exporter.metrics_dhcp6_subnet_ignore)

        # Check DDNS metrics setup (new feature)
        self.assertIsNotNone(exporter.metrics_ddns)
        self.assertIsNotNone(exporter.metrics_ddns_map)
        self.assertIsNotNone(exporter.ddns_key_pattern)

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


class TestExporterSetupDDNSMetrics(unittest.TestCase):
    """Test DDNS metrics setup (new feature)"""

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_setup_ddns_metrics_creates_global_metrics(self, mock_http):
        """Test that DDNS global metrics are created"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Check global DDNS metrics exist
        self.assertIn("ncr_error", exporter.metrics_ddns)
        self.assertIn("ncr_invalid", exporter.metrics_ddns)
        self.assertIn("ncr_received", exporter.metrics_ddns)
        self.assertIn("queue_full", exporter.metrics_ddns)
        self.assertIn("update_error", exporter.metrics_ddns)
        self.assertIn("update_sent", exporter.metrics_ddns)
        self.assertIn("update_signed", exporter.metrics_ddns)
        self.assertIn("update_success", exporter.metrics_ddns)
        self.assertIn("update_timeout", exporter.metrics_ddns)
        self.assertIn("update_unsigned", exporter.metrics_ddns)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_setup_ddns_metrics_creates_per_key_metrics(self, mock_http):
        """Test that DDNS per-key metrics are created"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Check per-key DDNS metrics exist
        self.assertIn("key_update_error", exporter.metrics_ddns)
        self.assertIn("key_update_sent", exporter.metrics_ddns)
        self.assertIn("key_update_success", exporter.metrics_ddns)
        self.assertIn("key_update_timeout", exporter.metrics_ddns)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_setup_ddns_metrics_has_server_label(self, mock_http):
        """Test that DDNS metrics include server label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Check that metrics have server label
        ncr_error_metric = exporter.metrics_ddns["ncr_error"]
        self.assertIn("server", ncr_error_metric._labelnames)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_setup_ddns_metrics_per_key_has_key_label(self, mock_http):
        """Test that per-key DDNS metrics include key label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Check that per-key metrics have both server and key labels
        key_metric = exporter.metrics_ddns["key_update_sent"]
        self.assertIn("server", key_metric._labelnames)
        self.assertIn("key", key_metric._labelnames)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_setup_ddns_metrics_map(self, mock_http):
        """Test DDNS metrics mapping"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Check metric mappings exist
        expected_mappings = {
            "ncr-error": "ncr_error",
            "ncr-invalid": "ncr_invalid",
            "ncr-received": "ncr_received",
            "queue-mgr-queue-full": "queue_full",
            "update-error": "update_error",
            "update-sent": "update_sent",
            "update-signed": "update_signed",
            "update-success": "update_success",
            "update-timeout": "update_timeout",
            "update-unsigned": "update_unsigned",
        }

        for kea_name, metric_name in expected_mappings.items():
            self.assertIn(kea_name, exporter.metrics_ddns_map)
            self.assertEqual(exporter.metrics_ddns_map[kea_name]["metric"], metric_name)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_setup_ddns_key_pattern(self, mock_http):
        """Test DDNS per-key pattern regex"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Test pattern matches expected formats
        pattern = exporter.ddns_key_pattern

        # Should match: key[domain.name.].metric-name
        match = pattern.match("key[example.com.].update-sent")
        self.assertIsNotNone(match)
        self.assertEqual(match.group("key"), "example.com.")
        self.assertEqual(match.group("metric"), "update-sent")

        # Should match with complex domain
        match = pattern.match("key[sub.domain.example.org.].update-success")
        self.assertIsNotNone(match)
        self.assertEqual(match.group("key"), "sub.domain.example.org.")

        # Should not match without brackets
        match = pattern.match("update-sent")
        self.assertIsNone(match)


class TestExporterServerLabeling(unittest.TestCase):
    """Test server labeling feature (new in this branch)"""

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_dhcp4_metrics_have_server_label(self, mock_http):
        """Test that DHCP4 metrics include server label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Check sent_packets metric has server label
        sent_packets = exporter.metrics_dhcp4["sent_packets"]
        self.assertIn("server", sent_packets._labelnames)
        self.assertIn("operation", sent_packets._labelnames)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_dhcp6_metrics_have_server_label(self, mock_http):
        """Test that DHCP6 metrics include server label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Check sent_packets metric has server label
        sent_packets = exporter.metrics_dhcp6["sent_packets"]
        self.assertIn("server", sent_packets._labelnames)
        self.assertIn("operation", sent_packets._labelnames)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_dhcp4_subnet_metrics_have_server_label(self, mock_http):
        """Test that DHCP4 subnet metrics include server label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Check subnet metrics have server label
        addresses_assigned = exporter.metrics_dhcp4["addresses_assigned_total"]
        self.assertIn("server", addresses_assigned._labelnames)
        self.assertIn("subnet", addresses_assigned._labelnames)
        self.assertIn("subnet_id", addresses_assigned._labelnames)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_dhcp6_subnet_metrics_have_server_label(self, mock_http):
        """Test that DHCP6 subnet metrics include server label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Check subnet metrics have server label
        na_assigned = exporter.metrics_dhcp6["na_assigned_total"]
        self.assertIn("server", na_assigned._labelnames)
        self.assertIn("subnet", na_assigned._labelnames)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_dhcp6_addr_reg_gauge_registered(self, mock_http):
        """na_registered_total gauge exists with correct labels (Kea 2.5.5+)"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        na_registered = exporter.metrics_dhcp6["na_registered_total"]
        self.assertIn("server", na_registered._labelnames)
        self.assertIn("subnet", na_registered._labelnames)
        self.assertIn("subnet_id", na_registered._labelnames)
        self.assertIn("pool", na_registered._labelnames)


class TestExporterParseMetrics(unittest.TestCase):
    """Test parse_metrics method"""

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_dhcp4(self, mock_http):
        """Test parsing DHCP4 metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        arguments = {
            "pkt4-discover-received": [[100, "2024-01-01 00:00:00"]],
            "pkt4-ack-sent": [[50, "2024-01-01 00:00:00"]],
        }
        subnets = {}

        # Mock metrics to capture calls
        mock_received = Mock()
        mock_received._labelnames = ["server", "operation"]
        mock_received_labels = Mock(return_value=mock_received)
        mock_received.labels = mock_received_labels
        exporter.metrics_dhcp4["received_packets"] = mock_received

        mock_sent = Mock()
        mock_sent._labelnames = ["server", "operation"]
        mock_sent_labels = Mock(return_value=mock_sent)
        mock_sent.labels = mock_sent_labels
        exporter.metrics_dhcp4["sent_packets"] = mock_sent

        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)

        # Verify received_packets was called with correct labels and value
        mock_received_labels.assert_called_once_with(server=server_id, operation="discover")
        mock_received.set.assert_called_once_with(100)

        # Verify sent_packets was called with correct labels and value
        mock_sent_labels.assert_called_once_with(server=server_id, operation="ack")
        mock_sent.set.assert_called_once_with(50)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_dhcp6(self, mock_http):
        """Test parsing DHCP6 metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        arguments = {
            "pkt6-solicit-received": [[200, "2024-01-01 00:00:00"]],
            "pkt6-reply-sent": [[150, "2024-01-01 00:00:00"]],
        }
        subnets = {}

        # Mock metrics to capture calls
        mock_received = Mock()
        mock_received._labelnames = ["server", "operation"]
        mock_received_labels = Mock(return_value=mock_received)
        mock_received.labels = mock_received_labels
        exporter.metrics_dhcp6["received_packets"] = mock_received

        mock_sent = Mock()
        mock_sent._labelnames = ["server", "operation"]
        mock_sent_labels = Mock(return_value=mock_sent)
        mock_sent.labels = mock_sent_labels
        exporter.metrics_dhcp6["sent_packets"] = mock_sent

        exporter.parse_metrics(server_id, DHCPVersion.DHCP6, arguments, subnets)

        # Verify received_packets was called with correct labels and value
        mock_received_labels.assert_called_once_with(server=server_id, operation="solicit")
        mock_received.set.assert_called_once_with(200)

        # Verify sent_packets was called with correct labels and value
        mock_sent_labels.assert_called_once_with(server=server_id, operation="reply")
        mock_sent.set.assert_called_once_with(150)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_dhcp6_addr_reg_packets(self, mock_http):
        """Test parsing DHCPv6 address registration packet metrics (Kea 2.5.5+)"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        arguments = {
            "pkt6-addr-reg-inform-received": [[10, "2024-01-01 00:00:00"]],
            "pkt6-addr-reg-reply-received": [[8, "2024-01-01 00:00:00"]],
            "pkt6-addr-reg-reply-sent": [[9, "2024-01-01 00:00:00"]],
        }
        subnets = {}

        mock_received = Mock()
        mock_received._labelnames = ["server", "operation"]
        mock_received.labels = Mock(return_value=mock_received)
        exporter.metrics_dhcp6["received_packets"] = mock_received

        mock_sent = Mock()
        mock_sent._labelnames = ["server", "operation"]
        mock_sent.labels = Mock(return_value=mock_sent)
        exporter.metrics_dhcp6["sent_packets"] = mock_sent

        exporter.parse_metrics(server_id, DHCPVersion.DHCP6, arguments, subnets)

        mock_received.labels.assert_any_call(server=server_id, operation="addr-reg-inform")
        mock_received.labels.assert_any_call(server=server_id, operation="addr-reg-reply")
        mock_sent.labels.assert_called_once_with(server=server_id, operation="addr-reg-reply")

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_dhcp6_registered_nas_subnet(self, mock_http):
        """Test parsing DHCPv6 registered-nas at subnet level (Kea 2.5.5+)"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        subnet_id = 5
        subnets = {subnet_id: {"subnet": "2001:db8::/48", "pools": [{"pool": "2001:db8::1-2001:db8::ff"}]}}
        arguments = {f"subnet[{subnet_id}].pool[0].registered-nas": [[7, "2024-01-01 00:00:00"]]}

        mock_gauge = Mock()
        mock_gauge._labelnames = ["server", "subnet", "subnet_id", "pool"]
        mock_gauge.labels = Mock(return_value=mock_gauge)
        exporter.metrics_dhcp6["na_registered_total"] = mock_gauge

        exporter.parse_metrics(server_id, DHCPVersion.DHCP6, arguments, subnets)

        mock_gauge.labels.assert_called_once_with(
            server=server_id,
            subnet="2001:db8::/48",
            subnet_id=subnet_id,
            pool="2001:db8::1-2001:db8::ff",
        )
        mock_gauge.set.assert_called_once_with(7)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_dhcp6_cumulative_registered_nas_ignored(self, mock_http):
        """cumulative-registered-nas at global and subnet level should be silently ignored"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        subnet_id = 5
        subnets = {subnet_id: {"subnet": "2001:db8::/48", "pools": []}}
        arguments = {
            "cumulative-registered-nas": [[100, "2024-01-01 00:00:00"]],
            f"subnet[{subnet_id}].cumulative-registered-nas": [[50, "2024-01-01 00:00:00"]],
        }

        # Should not add anything to unhandled_metrics
        exporter.parse_metrics(server_id, DHCPVersion.DHCP6, arguments, subnets)
        self.assertNotIn("cumulative-registered-nas", exporter.unhandled_metrics)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_ddns(self, mock_http):
        """Test parsing DDNS metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        arguments = {
            "ncr-received": [[150, "2024-01-01 00:00:00"]],
            "update-sent": [[100, "2024-01-01 00:00:00"]],
            "update-success": [[95, "2024-01-01 00:00:00"]],
        }
        subnets = {}

        # Mock metrics to capture calls
        mock_ncr_received = Mock()
        mock_ncr_received._labelnames = ["server"]
        mock_ncr_labels = Mock(return_value=mock_ncr_received)
        mock_ncr_received.labels = mock_ncr_labels
        exporter.metrics_ddns["ncr_received"] = mock_ncr_received

        mock_update_sent = Mock()
        mock_update_sent._labelnames = ["server"]
        mock_sent_labels = Mock(return_value=mock_update_sent)
        mock_update_sent.labels = mock_sent_labels
        exporter.metrics_ddns["update_sent"] = mock_update_sent

        mock_update_success = Mock()
        mock_update_success._labelnames = ["server"]
        mock_success_labels = Mock(return_value=mock_update_success)
        mock_update_success.labels = mock_success_labels
        exporter.metrics_ddns["update_success"] = mock_update_success

        exporter.parse_metrics(server_id, DHCPVersion.DDNS, arguments, subnets)

        # Verify metrics were called with correct server labels and values
        mock_ncr_labels.assert_called_once_with(server=server_id)
        mock_ncr_received.set.assert_called_once_with(150)

        mock_sent_labels.assert_called_once_with(server=server_id)
        mock_update_sent.set.assert_called_once_with(100)

        mock_success_labels.assert_called_once_with(server=server_id)
        mock_update_success.set.assert_called_once_with(95)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_ddns_per_key(self, mock_http):
        """Test parsing DDNS per-key metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        arguments = {
            "key[example.com.].update-sent": [[50, "2024-01-01 00:00:00"]],
            "key[example.com.].update-success": [[45, "2024-01-01 00:00:00"]],
            "key[example.com.].update-error": [[5, "2024-01-01 00:00:00"]],
            "key[test.org.].update-sent": [[30, "2024-01-01 00:00:00"]],
        }
        subnets = {}

        # Mock per-key metrics to capture calls
        mock_key_sent = Mock()
        mock_key_sent._labelnames = ["server", "key"]
        mock_sent_labels = Mock(return_value=mock_key_sent)
        mock_key_sent.labels = mock_sent_labels
        exporter.metrics_ddns["key_update_sent"] = mock_key_sent

        mock_key_success = Mock()
        mock_key_success._labelnames = ["server", "key"]
        mock_success_labels = Mock(return_value=mock_key_success)
        mock_key_success.labels = mock_success_labels
        exporter.metrics_ddns["key_update_success"] = mock_key_success

        mock_key_error = Mock()
        mock_key_error._labelnames = ["server", "key"]
        mock_error_labels = Mock(return_value=mock_key_error)
        mock_key_error.labels = mock_error_labels
        exporter.metrics_ddns["key_update_error"] = mock_key_error

        exporter.parse_metrics(server_id, DHCPVersion.DDNS, arguments, subnets)

        # Verify per-key metrics were called with correct labels and values
        # For example.com. key
        self.assertEqual(mock_sent_labels.call_count, 2)
        mock_sent_labels.assert_any_call(server=server_id, key="example.com.")
        mock_sent_labels.assert_any_call(server=server_id, key="test.org.")
        self.assertEqual(mock_key_sent.set.call_count, 2)

        mock_success_labels.assert_called_once_with(server=server_id, key="example.com.")
        mock_key_success.set.assert_called_once_with(45)

        mock_error_labels.assert_called_once_with(server=server_id, key="example.com.")
        mock_key_error.set.assert_called_once_with(5)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_parse_metrics_unhandled_ddns_per_key_metric(self, mock_echo, mock_http):
        """Test handling of unknown DDNS per-key metric"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        arguments = {"key[example.com.].unknown-metric": [[10, "2024-01-01 00:00:00"]]}
        subnets = {}

        exporter.parse_metrics(server_id, DHCPVersion.DDNS, arguments, subnets)

        # Should have echoed a message about unhandled metric
        mock_echo.assert_called()
        call_args = mock_echo.call_args[0][0]
        self.assertIn("Unhandled DDNS per-key metric", call_args)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_parse_metrics_unhandled_metric_only_once(self, mock_echo, mock_http):
        """Test that unhandled metrics are only reported once"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        arguments = {
            "unknown-metric-1": [[10, "2024-01-01 00:00:00"]],
            "unknown-metric-2": [[20, "2024-01-01 00:00:00"]],
        }
        subnets = {}

        # Parse twice
        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)
        # First call should have reported both unknown metrics
        self.assertGreater(mock_echo.call_count, 0)
        mock_echo.reset_mock()
        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)

        # Should not echo again for the same metrics
        self.assertEqual(mock_echo.call_count, 0)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_server_label_included(self, mock_http):
        """Test that server label is included in metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://server1:8000"
        arguments = {"pkt4-ack-sent": [[100, "2024-01-01 00:00:00"]]}
        subnets = {}

        # Mock the metric to capture labels
        mock_metric = Mock()
        mock_metric._labelnames = ["server", "operation"]
        mock_labels = Mock(return_value=mock_metric)
        mock_metric.labels = mock_labels
        exporter.metrics_dhcp4["sent_packets"] = mock_metric

        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)

        # Verify server label was passed
        mock_labels.assert_called()
        call_kwargs = mock_labels.call_args[1]
        self.assertEqual(call_kwargs["server"], "http://server1:8000")

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_ignores_global_ignore_list(self, mock_http):
        """Test that global ignore list is respected"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Get an ignored metric from the ignore list
        ignored_metric = exporter.metrics_dhcp4_global_ignore[0]

        server_id = "http://localhost:8000"
        arguments = {ignored_metric: [[100, "2024-01-01 00:00:00"]]}
        subnets = {}

        # Should not raise an error about unhandled metric
        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_unknown_dhcp_version_returns_early(self, mock_http):
        """Test that an unknown dhcp_version causes early return"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        # Should not raise - just return
        exporter.parse_metrics("server", "UNKNOWN", {"key": [[1, "ts"]]}, {})

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_subnet_metric(self, mock_http):
        """Test parsing a subnet-level metric with subnet context"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        subnets = {1: {"subnet": "192.168.1.0/24", "pools": []}}
        arguments = {"subnet[1].assigned-addresses": [[42, "2024-01-01 00:00:00"]]}

        mock_metric = Mock()
        mock_metric._labelnames = ["server", "subnet", "subnet_id", "pool"]
        mock_metric.labels.return_value = mock_metric
        exporter.metrics_dhcp4["addresses_assigned_total"] = mock_metric

        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)

        mock_metric.labels.assert_called_once_with(server=server_id, subnet="192.168.1.0/24", subnet_id=1, pool="")
        mock_metric.set.assert_called_once_with(42)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_pool_metric(self, mock_http):
        """Test parsing a pool-level metric with pool context"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        subnets = {1: {"subnet": "192.168.1.0/24", "pools": [{"pool": "192.168.1.10-192.168.1.50"}]}}
        arguments = {"subnet[1].pool[0].assigned-addresses": [[10, "2024-01-01 00:00:00"]]}

        mock_metric = Mock()
        mock_metric._labelnames = ["server", "subnet", "subnet_id", "pool"]
        mock_metric.labels.return_value = mock_metric
        exporter.metrics_dhcp4["addresses_assigned_total"] = mock_metric

        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)

        mock_metric.labels.assert_called_once_with(
            server=server_id, subnet="192.168.1.0/24", subnet_id=1, pool="192.168.1.10-192.168.1.50"
        )
        mock_metric.set.assert_called_once_with(10)

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_parse_metrics_missing_subnet(self, mock_echo, mock_http):
        """Test that a metric for a vanished subnet is skipped and logged once"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        subnets = {}  # subnet 99 not present
        arguments = {"subnet[99].assigned-addresses": [[1, "2024-01-01 00:00:00"]]}

        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)
        mock_echo.assert_called_once()
        self.assertIn("subnet vanished", mock_echo.call_args[0][0])

        # Second call should not log again
        mock_echo.reset_mock()
        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)
        mock_echo.assert_not_called()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    @patch("click.echo")
    def test_parse_metrics_missing_pool(self, mock_echo, mock_http):
        """Test that a metric for a vanished pool is skipped and logged once"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        subnets = {1: {"subnet": "192.168.1.0/24", "pools": []}}  # no pools
        arguments = {"subnet[1].pool[0].assigned-addresses": [[1, "2024-01-01 00:00:00"]]}

        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)
        mock_echo.assert_called_once()
        self.assertIn("subnet vanished", mock_echo.call_args[0][0])

        # Second call should not log again
        mock_echo.reset_mock()
        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)
        mock_echo.assert_not_called()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_parse_metrics_subnet_ignore_list(self, mock_http):
        """Test that subnet-level ignore list is respected"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        server_id = "http://localhost:8000"
        ignored = exporter.metrics_dhcp4_subnet_ignore[0]
        subnets = {1: {"subnet": "192.168.1.0/24", "pools": []}}
        arguments = {f"subnet[1].{ignored}": [[1, "2024-01-01 00:00:00"]]}

        # Should not raise or try to export
        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)


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


class TestExporterSubnetPattern(unittest.TestCase):
    """Test subnet pattern regex"""

    def setUp(self):
        self.registry = CollectorRegistry()

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_subnet_pattern_pool_metric(self, mock_http):
        """Test subnet pattern matches pool metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        pattern = exporter.subnet_pattern
        match = pattern.match("subnet[1].pool[0].assigned-addresses")

        self.assertIsNotNone(match)
        self.assertEqual(match.group("subnet_id"), "1")
        self.assertEqual(match.group("pool_index"), "0")
        self.assertEqual(match.group("pool_metric"), "assigned-addresses")
        self.assertIsNone(match.group("subnet_metric"))

    @patch("kea_exporter.exporter.KeaHTTPClient")
    def test_subnet_pattern_subnet_metric(self, mock_http):
        """Test subnet pattern matches subnet-level metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=["http://localhost:8000"], registry=self.registry)

        pattern = exporter.subnet_pattern
        match = pattern.match("subnet[5].total-addresses")

        self.assertIsNotNone(match)
        self.assertEqual(match.group("subnet_id"), "5")
        self.assertIsNone(match.group("pool_index"))
        self.assertIsNone(match.group("pool_metric"))
        self.assertEqual(match.group("subnet_metric"), "total-addresses")


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


if __name__ == "__main__":
    unittest.main()
