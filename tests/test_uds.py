"""
Tests for kea_exporter.uds module
"""

import unittest
from unittest.mock import MagicMock, patch
import os
import json

from kea_exporter.uds import KeaSocketClient, KeaConfigError
from kea_exporter import DHCPVersion


class TestKeaSocketClientInit(unittest.TestCase):
    """Test KeaSocketClient initialization"""

    @patch("os.access")
    def test_init_socket_not_found(self, mock_access):
        """Test initialization with non-existent socket"""
        mock_access.side_effect = lambda path, mode: False if mode == os.F_OK else True

        with self.assertRaises(FileNotFoundError) as context:
            KeaSocketClient("/nonexistent/socket")

        self.assertIn("does not exist", str(context.exception))

    @patch("os.access")
    def test_init_socket_no_permissions(self, mock_access):
        """Test initialization with socket without permissions"""

        def access_check(path, mode):
            if mode == os.F_OK:
                return True
            if mode == (os.R_OK | os.W_OK):
                return False
            return True

        mock_access.side_effect = access_check

        with self.assertRaises(PermissionError) as context:
            KeaSocketClient("/path/to/socket")

        self.assertIn("No read/write permissions", str(context.exception))

    @patch("os.access")
    @patch("os.path.abspath")
    def test_init_success(self, mock_abspath, mock_access):
        """Test successful initialization"""
        mock_access.return_value = True
        mock_abspath.return_value = "/abs/path/to/socket"

        client = KeaSocketClient("/path/to/socket")

        self.assertEqual(client.sock_path, "/abs/path/to/socket")
        self.assertEqual(client._server_id, "/abs/path/to/socket")
        self.assertIsNone(client.version)
        self.assertIsNone(client.config)
        self.assertIsNone(client.subnets)
        self.assertEqual(client.subnet_missing_info_sent, [])
        self.assertIsNone(client.dhcp_version)

    @patch("os.access")
    @patch("os.path.abspath")
    def test_server_id_matches_socket_path(self, mock_abspath, mock_access):
        """Test that server_id is set to socket path"""
        mock_access.return_value = True
        mock_abspath.return_value = "/var/run/kea/control.sock"

        client = KeaSocketClient("/var/run/kea/control.sock")

        self.assertEqual(client._server_id, "/var/run/kea/control.sock")


class TestKeaSocketClientQuery(unittest.TestCase):
    """Test KeaSocketClient.query method"""

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_query_success(self, mock_abspath, mock_access, mock_socket_class):
        """Test successful query"""
        mock_access.return_value = True
        mock_abspath.return_value = "/path/to/socket"

        # Mock socket
        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        # Mock makefile to return JSON response
        mock_file = MagicMock()
        mock_file.read.return_value = json.dumps({"result": 0, "arguments": {"test": "data"}})
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient("/path/to/socket")
        result = client.query("test-command")

        self.assertEqual(result["result"], 0)
        self.assertEqual(result["arguments"]["test"], "data")

        # Verify socket operations
        mock_sock.connect.assert_called_once_with("/path/to/socket")
        mock_sock.send.assert_called_once()

        # Verify command format
        sent_data = mock_sock.send.call_args[0][0]
        sent_command = json.loads(sent_data.decode("utf-8"))
        self.assertEqual(sent_command["command"], "test-command")

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_query_failure_result(self, mock_abspath, mock_access, mock_socket_class):
        """Test query with non-zero result"""
        mock_access.return_value = True
        mock_abspath.return_value = "/path/to/socket"

        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        mock_file = MagicMock()
        mock_file.read.return_value = json.dumps({"result": 1, "text": "Error message"})
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient("/path/to/socket")

        with self.assertRaises(ValueError):
            client.query("failing-command")


class TestKeaSocketClientReload(unittest.TestCase):
    """Test KeaSocketClient.reload method"""

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_reload_dhcp4_config(self, mock_abspath, mock_access, mock_socket_class):
        """Test reload with DHCP4 configuration"""
        mock_access.return_value = True
        mock_abspath.return_value = "/path/to/socket"

        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        config_data = {
            "result": 0,
            "arguments": {
                "Dhcp4": {"subnet4": [{"id": 1, "subnet": "192.168.1.0/24"}, {"id": 2, "subnet": "192.168.2.0/24"}]}
            },
        }

        mock_file = MagicMock()
        mock_file.read.return_value = json.dumps(config_data)
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient("/path/to/socket")
        client.reload()

        self.assertEqual(client.dhcp_version, DHCPVersion.DHCP4)
        self.assertEqual(len(client.subnets), 2)
        self.assertIn(1, client.subnets)
        self.assertIn(2, client.subnets)
        self.assertEqual(client.subnets[1]["subnet"], "192.168.1.0/24")

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_reload_dhcp6_config(self, mock_abspath, mock_access, mock_socket_class):
        """Test reload with DHCP6 configuration"""
        mock_access.return_value = True
        mock_abspath.return_value = "/path/to/socket"

        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        config_data = {"result": 0, "arguments": {"Dhcp6": {"subnet6": [{"id": 10, "subnet": "2001:db8::/64"}]}}}

        mock_file = MagicMock()
        mock_file.read.return_value = json.dumps(config_data)
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient("/path/to/socket")
        client.reload()

        self.assertEqual(client.dhcp_version, DHCPVersion.DHCP6)
        self.assertEqual(len(client.subnets), 1)
        self.assertIn(10, client.subnets)

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_reload_unsupported_config(self, mock_abspath, mock_access, mock_socket_class):
        """Test reload with unsupported configuration"""
        mock_access.return_value = True
        mock_abspath.return_value = "/path/to/socket"

        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        config_data = {"result": 0, "arguments": {"UnknownService": {}}}

        mock_file = MagicMock()
        mock_file.read.return_value = json.dumps(config_data)
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient("/path/to/socket")

        with self.assertRaises(KeaConfigError):
            client.reload()


class TestKeaSocketClientStats(unittest.TestCase):
    """Test KeaSocketClient.stats method"""

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_stats_dhcp4(self, mock_abspath, mock_access, mock_socket_class):
        """Test stats for DHCP4"""
        mock_access.return_value = True
        mock_abspath.return_value = "/path/to/socket"

        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        config_data = {"result": 0, "arguments": {"Dhcp4": {"subnet4": [{"id": 1, "subnet": "192.168.1.0/24"}]}}}

        stats_data = {
            "result": 0,
            "arguments": {"pkt4-received": [[100, "2024-01-01"]], "pkt4-ack-sent": [[50, "2024-01-01"]]},
        }

        mock_file = MagicMock()
        mock_file.read.side_effect = [json.dumps(config_data), json.dumps(stats_data)]
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient("/path/to/socket")
        results = list(client.stats())

        self.assertEqual(len(results), 1)
        server_id, dhcp_version, arguments, subnets = results[0]

        self.assertEqual(server_id, "/path/to/socket")
        self.assertEqual(dhcp_version, DHCPVersion.DHCP4)
        self.assertIn("pkt4-received", arguments)
        self.assertEqual(len(subnets), 1)

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_stats_dhcp6(self, mock_abspath, mock_access, mock_socket_class):
        """Test stats for DHCP6"""
        mock_access.return_value = True
        mock_abspath.return_value = "/path/to/socket"

        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        config_data = {"result": 0, "arguments": {"Dhcp6": {"subnet6": [{"id": 10, "subnet": "2001:db8::/64"}]}}}

        stats_data = {"result": 0, "arguments": {"pkt6-received": [[200, "2024-01-01"]]}}

        mock_file = MagicMock()
        mock_file.read.side_effect = [json.dumps(config_data), json.dumps(stats_data)]
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient("/path/to/socket")
        results = list(client.stats())

        _server_id, dhcp_version, arguments, _subnets = results[0]

        self.assertEqual(dhcp_version, DHCPVersion.DHCP6)
        self.assertIn("pkt6-received", arguments)

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_stats_calls_reload(self, mock_abspath, mock_access, mock_socket_class):
        """Test that stats calls reload to refresh configuration"""
        mock_access.return_value = True
        mock_abspath.return_value = "/path/to/socket"

        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        config_data = {"result": 0, "arguments": {"Dhcp4": {"subnet4": []}}}

        stats_data = {"result": 0, "arguments": {}}

        mock_file = MagicMock()
        mock_file.read.side_effect = [json.dumps(config_data), json.dumps(stats_data)]  # First reload  # Stats query
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient("/path/to/socket")
        list(client.stats())

        # Should have made two queries: config-get and statistic-get-all
        self.assertEqual(mock_sock.send.call_count, 2)

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_stats_server_id_is_socket_path(self, mock_abspath, mock_access, mock_socket_class):
        """Test that stats returns socket path as server_id"""
        mock_access.return_value = True
        socket_path = "/var/run/kea/control.sock"
        mock_abspath.return_value = socket_path

        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        config_data = {"result": 0, "arguments": {"Dhcp4": {"subnet4": []}}}

        stats_data = {"result": 0, "arguments": {}}

        mock_file = MagicMock()
        mock_file.read.side_effect = [json.dumps(config_data), json.dumps(stats_data)]
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient(socket_path)
        results = list(client.stats())

        server_id = results[0][0]
        self.assertEqual(server_id, socket_path)


class TestKeaSocketClientEdgeCases(unittest.TestCase):
    """Test edge cases for KeaSocketClient"""

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_empty_subnets(self, mock_abspath, mock_access, mock_socket_class):
        """Test handling of empty subnet list"""
        mock_access.return_value = True
        mock_abspath.return_value = "/path/to/socket"

        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        config_data = {"result": 0, "arguments": {"Dhcp4": {"subnet4": []}}}

        mock_file = MagicMock()
        mock_file.read.return_value = json.dumps(config_data)
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient("/path/to/socket")
        client.reload()

        self.assertEqual(client.subnets, {})

    @patch("socket.socket")
    @patch("os.access")
    @patch("os.path.abspath")
    def test_subnet_map_creation(self, mock_abspath, mock_access, mock_socket_class):
        """Test that subnets are correctly mapped by ID"""
        mock_access.return_value = True
        mock_abspath.return_value = "/path/to/socket"

        mock_sock = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        config_data = {
            "result": 0,
            "arguments": {
                "Dhcp4": {
                    "subnet4": [
                        {"id": 5, "subnet": "10.0.5.0/24", "pools": []},
                        {"id": 10, "subnet": "10.0.10.0/24", "pools": []},
                        {"id": 15, "subnet": "10.0.15.0/24", "pools": []},
                    ]
                }
            },
        }

        mock_file = MagicMock()
        mock_file.read.return_value = json.dumps(config_data)
        mock_sock.makefile.return_value = mock_file

        client = KeaSocketClient("/path/to/socket")
        client.reload()

        # Verify subnet map has all IDs
        self.assertEqual(set(client.subnets.keys()), {5, 10, 15})
        # Verify each ID maps to correct subnet data
        self.assertEqual(client.subnets[5]["subnet"], "10.0.5.0/24")
        self.assertEqual(client.subnets[10]["subnet"], "10.0.10.0/24")
        self.assertEqual(client.subnets[15]["subnet"], "10.0.15.0/24")


if __name__ == "__main__":
    unittest.main()
