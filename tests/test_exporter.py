"""
Tests for kea_exporter.exporter module
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
import re

from kea_exporter.exporter import Exporter
from kea_exporter import DHCPVersion


class TestExporterInit(unittest.TestCase):
    """Test Exporter initialization"""

    @patch('kea_exporter.exporter.KeaHTTPClient')
    @patch('kea_exporter.exporter.KeaSocketClient')
    def test_init_creates_metrics(self, mock_socket, mock_http):
        """Test that initialization creates all metric structures"""
        mock_http.return_value = Mock()

        exporter = Exporter(targets=['http://localhost:8000'])

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

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_init_http_target(self, mock_http):
        """Test initialization with HTTP target"""
        mock_client = Mock()
        mock_http.return_value = mock_client

        exporter = Exporter(targets=['http://localhost:8000'])

        self.assertEqual(len(exporter.targets), 1)
        self.assertEqual(exporter.targets[0], mock_client)
        mock_http.assert_called_once()

    @patch('kea_exporter.exporter.KeaSocketClient')
    def test_init_socket_target(self, mock_socket):
        """Test initialization with Unix socket target"""
        mock_client = Mock()
        mock_socket.return_value = mock_client

        exporter = Exporter(targets=['/var/run/kea/control.sock'])

        self.assertEqual(len(exporter.targets), 1)
        self.assertEqual(exporter.targets[0], mock_client)
        mock_socket.assert_called_once()

    @patch('kea_exporter.exporter.KeaHTTPClient')
    @patch('kea_exporter.exporter.KeaSocketClient')
    def test_init_multiple_targets(self, mock_socket, mock_http):
        """Test initialization with multiple targets"""
        mock_http_client = Mock()
        mock_socket_client = Mock()
        mock_http.return_value = mock_http_client
        mock_socket.return_value = mock_socket_client

        exporter = Exporter(targets=[
            'http://localhost:8000',
            '/var/run/kea/socket1',
            'http://remote:8001'
        ])

        self.assertEqual(len(exporter.targets), 3)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_init_passes_timeout_to_http_client(self, mock_http):
        """Test that timeout is passed to HTTP client"""
        mock_http.return_value = Mock()

        exporter = Exporter(targets=['http://localhost:8000'], timeout=30)

        mock_http.assert_called_once()
        call_kwargs = mock_http.call_args[1]
        self.assertEqual(call_kwargs['timeout'], 30)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    @patch('click.echo')
    def test_init_handles_invalid_target(self, mock_echo, mock_http):
        """Test handling of invalid target format"""
        mock_http.side_effect = OSError("Connection failed")

        exporter = Exporter(targets=['http://invalid:8000'])

        # Should have echoed error but not crashed
        mock_echo.assert_called()
        self.assertEqual(len(exporter.targets), 0)


class TestExporterSetupDDNSMetrics(unittest.TestCase):
    """Test DDNS metrics setup (new feature)"""

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_setup_ddns_metrics_creates_global_metrics(self, mock_http):
        """Test that DDNS global metrics are created"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Check global DDNS metrics exist
        self.assertIn('ncr_error', exporter.metrics_ddns)
        self.assertIn('ncr_invalid', exporter.metrics_ddns)
        self.assertIn('ncr_received', exporter.metrics_ddns)
        self.assertIn('queue_full', exporter.metrics_ddns)
        self.assertIn('update_error', exporter.metrics_ddns)
        self.assertIn('update_sent', exporter.metrics_ddns)
        self.assertIn('update_signed', exporter.metrics_ddns)
        self.assertIn('update_success', exporter.metrics_ddns)
        self.assertIn('update_timeout', exporter.metrics_ddns)
        self.assertIn('update_unsigned', exporter.metrics_ddns)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_setup_ddns_metrics_creates_per_key_metrics(self, mock_http):
        """Test that DDNS per-key metrics are created"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Check per-key DDNS metrics exist
        self.assertIn('key_update_error', exporter.metrics_ddns)
        self.assertIn('key_update_sent', exporter.metrics_ddns)
        self.assertIn('key_update_success', exporter.metrics_ddns)
        self.assertIn('key_update_timeout', exporter.metrics_ddns)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_setup_ddns_metrics_has_server_label(self, mock_http):
        """Test that DDNS metrics include server label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Check that metrics have server label
        ncr_error_metric = exporter.metrics_ddns['ncr_error']
        self.assertIn('server', ncr_error_metric._labelnames)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_setup_ddns_metrics_per_key_has_key_label(self, mock_http):
        """Test that per-key DDNS metrics include key label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Check that per-key metrics have both server and key labels
        key_metric = exporter.metrics_ddns['key_update_sent']
        self.assertIn('server', key_metric._labelnames)
        self.assertIn('key', key_metric._labelnames)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_setup_ddns_metrics_map(self, mock_http):
        """Test DDNS metrics mapping"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Check metric mappings exist
        expected_mappings = {
            'ncr-error': 'ncr_error',
            'ncr-invalid': 'ncr_invalid',
            'ncr-received': 'ncr_received',
            'queue-mgr-queue-full': 'queue_full',
            'update-error': 'update_error',
            'update-sent': 'update_sent',
            'update-signed': 'update_signed',
            'update-success': 'update_success',
            'update-timeout': 'update_timeout',
            'update-unsigned': 'update_unsigned',
        }

        for kea_name, metric_name in expected_mappings.items():
            self.assertIn(kea_name, exporter.metrics_ddns_map)
            self.assertEqual(exporter.metrics_ddns_map[kea_name]['metric'], metric_name)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_setup_ddns_key_pattern(self, mock_http):
        """Test DDNS per-key pattern regex"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Test pattern matches expected formats
        pattern = exporter.ddns_key_pattern

        # Should match: key[domain.name.].metric-name
        match = pattern.match("key[example.com.].update-sent")
        self.assertIsNotNone(match)
        self.assertEqual(match.group('key'), 'example.com.')
        self.assertEqual(match.group('metric'), 'update-sent')

        # Should match with complex domain
        match = pattern.match("key[sub.domain.example.org.].update-success")
        self.assertIsNotNone(match)
        self.assertEqual(match.group('key'), 'sub.domain.example.org.')

        # Should not match without brackets
        match = pattern.match("update-sent")
        self.assertIsNone(match)


class TestExporterServerLabeling(unittest.TestCase):
    """Test server labeling feature (new in this branch)"""

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_dhcp4_metrics_have_server_label(self, mock_http):
        """Test that DHCP4 metrics include server label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Check sent_packets metric has server label
        sent_packets = exporter.metrics_dhcp4['sent_packets']
        self.assertIn('server', sent_packets._labelnames)
        self.assertIn('operation', sent_packets._labelnames)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_dhcp6_metrics_have_server_label(self, mock_http):
        """Test that DHCP6 metrics include server label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Check sent_packets metric has server label
        sent_packets = exporter.metrics_dhcp6['sent_packets']
        self.assertIn('server', sent_packets._labelnames)
        self.assertIn('operation', sent_packets._labelnames)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_dhcp4_subnet_metrics_have_server_label(self, mock_http):
        """Test that DHCP4 subnet metrics include server label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Check subnet metrics have server label
        addresses_assigned = exporter.metrics_dhcp4['addresses_assigned_total']
        self.assertIn('server', addresses_assigned._labelnames)
        self.assertIn('subnet', addresses_assigned._labelnames)
        self.assertIn('subnet_id', addresses_assigned._labelnames)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_dhcp6_subnet_metrics_have_server_label(self, mock_http):
        """Test that DHCP6 subnet metrics include server label"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Check subnet metrics have server label
        na_assigned = exporter.metrics_dhcp6['na_assigned_total']
        self.assertIn('server', na_assigned._labelnames)
        self.assertIn('subnet', na_assigned._labelnames)


class TestExporterParseMetrics(unittest.TestCase):
    """Test parse_metrics method"""

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_parse_metrics_dhcp4(self, mock_http):
        """Test parsing DHCP4 metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        server_id = "http://localhost:8000"
        arguments = {
            "pkt4-received": [[100, "2024-01-01 00:00:00"]],
            "pkt4-ack-sent": [[50, "2024-01-01 00:00:00"]]
        }
        subnets = {}

        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)

        # Verify metrics were set (we can't easily verify values without mocking Gauge)
        # But we can verify the method didn't crash

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_parse_metrics_dhcp6(self, mock_http):
        """Test parsing DHCP6 metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        server_id = "http://localhost:8000"
        arguments = {
            "pkt6-received": [[200, "2024-01-01 00:00:00"]],
            "pkt6-reply-sent": [[150, "2024-01-01 00:00:00"]]
        }
        subnets = {}

        exporter.parse_metrics(server_id, DHCPVersion.DHCP6, arguments, subnets)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_parse_metrics_ddns(self, mock_http):
        """Test parsing DDNS metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        server_id = "http://localhost:8000"
        arguments = {
            "ncr-received": [[150, "2024-01-01 00:00:00"]],
            "update-sent": [[100, "2024-01-01 00:00:00"]],
            "update-success": [[95, "2024-01-01 00:00:00"]]
        }
        subnets = {}

        exporter.parse_metrics(server_id, DHCPVersion.DDNS, arguments, subnets)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_parse_metrics_ddns_per_key(self, mock_http):
        """Test parsing DDNS per-key metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        server_id = "http://localhost:8000"
        arguments = {
            "key[example.com.].update-sent": [[50, "2024-01-01 00:00:00"]],
            "key[example.com.].update-success": [[45, "2024-01-01 00:00:00"]],
            "key[example.com.].update-error": [[5, "2024-01-01 00:00:00"]],
            "key[test.org.].update-sent": [[30, "2024-01-01 00:00:00"]]
        }
        subnets = {}

        exporter.parse_metrics(server_id, DHCPVersion.DDNS, arguments, subnets)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    @patch('click.echo')
    def test_parse_metrics_unhandled_ddns_per_key_metric(self, mock_echo, mock_http):
        """Test handling of unknown DDNS per-key metric"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        server_id = "http://localhost:8000"
        arguments = {
            "key[example.com.].unknown-metric": [[10, "2024-01-01 00:00:00"]]
        }
        subnets = {}

        exporter.parse_metrics(server_id, DHCPVersion.DDNS, arguments, subnets)

        # Should have echoed a message about unhandled metric
        mock_echo.assert_called()
        call_args = mock_echo.call_args[0][0]
        self.assertIn("Unhandled DDNS per-key metric", call_args)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    @patch('click.echo')
    def test_parse_metrics_unhandled_metric_only_once(self, mock_echo, mock_http):
        """Test that unhandled metrics are only reported once"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        server_id = "http://localhost:8000"
        arguments = {
            "unknown-metric-1": [[10, "2024-01-01 00:00:00"]],
            "unknown-metric-2": [[20, "2024-01-01 00:00:00"]]
        }
        subnets = {}

        # Parse twice
        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)
        mock_echo.reset_mock()
        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)

        # Should not echo again for the same metrics
        self.assertEqual(mock_echo.call_count, 0)

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_parse_metrics_server_label_included(self, mock_http):
        """Test that server label is included in metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        server_id = "http://server1:8000"
        arguments = {
            "pkt4-received": [[100, "2024-01-01 00:00:00"]]
        }
        subnets = {}

        # Mock the metric to capture labels
        mock_metric = Mock()
        mock_labels = Mock(return_value=mock_metric)
        mock_metric.labels = mock_labels
        exporter.metrics_dhcp4['received_packets'] = mock_metric

        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)

        # Verify server label was passed
        mock_labels.assert_called()
        call_kwargs = mock_labels.call_args[1]
        self.assertEqual(call_kwargs['server'], "http://server1:8000")

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_parse_metrics_ignores_global_ignore_list(self, mock_http):
        """Test that global ignore list is respected"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        # Get an ignored metric from the ignore list
        ignored_metric = exporter.metrics_dhcp4_global_ignore[0]

        server_id = "http://localhost:8000"
        arguments = {
            ignored_metric: [[100, "2024-01-01 00:00:00"]]
        }
        subnets = {}

        # Should not raise an error about unhandled metric
        exporter.parse_metrics(server_id, DHCPVersion.DHCP4, arguments, subnets)


class TestExporterUpdate(unittest.TestCase):
    """Test Exporter.update method"""

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_update_calls_stats_on_all_targets(self, mock_http):
        """Test that update calls stats on all targets"""
        mock_client1 = Mock()
        mock_client1.stats.return_value = [
            ("http://server1:8000", DHCPVersion.DHCP4, {}, {})
        ]

        mock_client2 = Mock()
        mock_client2.stats.return_value = [
            ("http://server2:8000", DHCPVersion.DHCP6, {}, {})
        ]

        mock_http.side_effect = [mock_client1, mock_client2]

        exporter = Exporter(targets=['http://server1:8000', 'http://server2:8000'])
        exporter.update()

        # Both clients should have had stats() called
        mock_client1.stats.assert_called_once()
        mock_client2.stats.assert_called_once()

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_update_calls_parse_metrics(self, mock_http):
        """Test that update calls parse_metrics for each stats result"""
        mock_client = Mock()
        mock_client.stats.return_value = [
            ("http://server:8000", DHCPVersion.DHCP4, {"pkt4-received": [[100, "2024-01-01"]]}, {})
        ]

        mock_http.return_value = mock_client

        exporter = Exporter(targets=['http://server:8000'])

        with patch.object(exporter, 'parse_metrics') as mock_parse:
            exporter.update()

            mock_parse.assert_called_once()
            call_args = mock_parse.call_args[0]
            self.assertEqual(call_args[0], "http://server:8000")  # server_id
            self.assertEqual(call_args[1], DHCPVersion.DHCP4)  # dhcp_version


class TestExporterSubnetPattern(unittest.TestCase):
    """Test subnet pattern regex"""

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_subnet_pattern_pool_metric(self, mock_http):
        """Test subnet pattern matches pool metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        pattern = exporter.subnet_pattern
        match = pattern.match("subnet[1].pool[0].assigned-addresses")

        self.assertIsNotNone(match)
        self.assertEqual(match.group('subnet_id'), '1')
        self.assertEqual(match.group('pool_index'), '0')
        self.assertEqual(match.group('pool_metric'), 'assigned-addresses')
        self.assertIsNone(match.group('subnet_metric'))

    @patch('kea_exporter.exporter.KeaHTTPClient')
    def test_subnet_pattern_subnet_metric(self, mock_http):
        """Test subnet pattern matches subnet-level metrics"""
        mock_http.return_value = Mock()
        exporter = Exporter(targets=['http://localhost:8000'])

        pattern = exporter.subnet_pattern
        match = pattern.match("subnet[5].total-addresses")

        self.assertIsNotNone(match)
        self.assertEqual(match.group('subnet_id'), '5')
        self.assertIsNone(match.group('pool_index'))
        self.assertIsNone(match.group('pool_metric'))
        self.assertEqual(match.group('subnet_metric'), 'total-addresses')


if __name__ == '__main__':
    unittest.main()