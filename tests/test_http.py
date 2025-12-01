"""
Tests for kea_exporter.http module
"""

import unittest
from unittest.mock import Mock, patch

from kea_exporter.http import KeaHTTPClient
from kea_exporter import DHCPVersion


class TestKeaHTTPClientInit(unittest.TestCase):
    """Test KeaHTTPClient initialization"""

    @patch("kea_exporter.http.requests.post")
    def test_init_basic_url(self, mock_post):
        """Test initialization with basic HTTP URL"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {}}]
        mock_post.return_value = mock_response

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        self.assertEqual(client._target, "http://localhost:8000")
        self.assertEqual(client._server_id, "http://localhost:8000")
        self.assertIsNone(client._auth)
        self.assertIsNone(client._cert)
        self.assertEqual(client.timeout, 10)

    @patch("kea_exporter.http.requests.post")
    def test_init_with_basic_auth(self, mock_post):
        """Test initialization with embedded credentials"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {}}]
        mock_post.return_value = mock_response

        client = KeaHTTPClient(target="http://user:pass@localhost:8000", client_cert=None, client_key=None)

        # Credentials should be extracted and URL cleaned
        self.assertEqual(client._target, "http://localhost:8000")
        self.assertEqual(client._server_id, "http://localhost:8000")
        self.assertEqual(client._auth, ("user", "pass"))

    @patch("kea_exporter.http.requests.post")
    def test_init_with_basic_auth_no_port(self, mock_post):
        """Test initialization with credentials but no explicit port"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {}}]
        mock_post.return_value = mock_response

        client = KeaHTTPClient(target="http://admin:secret@example.com", client_cert=None, client_key=None)

        self.assertEqual(client._target, "http://example.com")
        self.assertEqual(client._server_id, "http://example.com")
        self.assertEqual(client._auth, ("admin", "secret"))

    @patch("kea_exporter.http.requests.post")
    def test_init_with_basic_auth_and_path(self, mock_post):
        """Test initialization with credentials and path"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {}}]
        mock_post.return_value = mock_response

        client = KeaHTTPClient(target="http://user:pass@localhost:8000/api", client_cert=None, client_key=None)

        self.assertEqual(client._target, "http://localhost:8000/api")
        self.assertEqual(client._auth, ("user", "pass"))

    @patch("kea_exporter.http.requests.post")
    def test_init_with_client_cert(self, mock_post):
        """Test initialization with client certificates"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {}}]
        mock_post.return_value = mock_response

        client = KeaHTTPClient(
            target="https://localhost:8000", client_cert="/path/to/cert.pem", client_key="/path/to/key.pem"
        )

        self.assertEqual(client._cert, ("/path/to/cert.pem", "/path/to/key.pem"))

    @patch("kea_exporter.http.requests.post")
    def test_init_with_custom_timeout(self, mock_post):
        """Test initialization with custom timeout"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {}}]
        mock_post.return_value = mock_response

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None, timeout=30)

        self.assertEqual(client.timeout, 30)

    @patch("kea_exporter.http.requests.post")
    def test_init_default_timeout(self, mock_post):
        """Test default timeout value"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {}}]
        mock_post.return_value = mock_response

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        self.assertEqual(client.timeout, 10)

    @patch("kea_exporter.http.requests.post")
    def test_init_calls_load_modules(self, mock_post):
        """Test that initialization calls load_modules"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {"dhcp4": {}}}]
        mock_post.return_value = mock_response

        KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        # load_modules should have been called (makes a POST request)
        self.assertTrue(mock_post.called)


class TestKeaHTTPClientLoadModules(unittest.TestCase):
    """Test KeaHTTPClient.load_modules method"""

    @patch("kea_exporter.http.requests.post")
    def test_load_modules_control_agent(self, mock_post):
        """Test loading modules via Control Agent discovery"""
        config_response = Mock()
        config_response.json.return_value = [
            {
                "arguments": {
                    "Control-agent": {
                        "control-sockets": {
                            "dhcp4": {"socket-type": "unix", "socket-name": "/tmp/kea4.sock"},
                            "dhcp6": {"socket-type": "unix", "socket-name": "/tmp/kea6.sock"},
                        }
                    }
                }
            }
        ]

        subnets_response = Mock()
        subnets_response.json.return_value = [
            {"arguments": {"Dhcp4": {"subnet4": []}}},
            {"arguments": {"Dhcp6": {"subnet6": []}}},
        ]

        mock_post.side_effect = [config_response, subnets_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        self.assertIn("dhcp4", client.modules)
        self.assertIn("dhcp6", client.modules)

    @patch("kea_exporter.http.requests.post")
    def test_load_modules_fallback_dhcp4(self, mock_post):
        """Test loading modules via fallback detection for DHCP4"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"Dhcp4": {"subnet4": []}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = [{"arguments": {"Dhcp4": {"subnet4": []}}}]

        mock_post.side_effect = [config_response, subnets_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        self.assertIn("dhcp4", client.modules)

    @patch("kea_exporter.http.requests.post")
    def test_load_modules_fallback_dhcp6(self, mock_post):
        """Test loading modules via fallback detection for DHCP6"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"Dhcp6": {"subnet6": []}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = [{"arguments": {"Dhcp6": {"subnet6": []}}}]

        mock_post.side_effect = [config_response, subnets_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        self.assertIn("dhcp6", client.modules)

    @patch("kea_exporter.http.requests.post")
    def test_load_modules_fallback_ddns(self, mock_post):
        """Test loading modules via fallback detection for DDNS"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"ddns": {}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = []

        mock_post.side_effect = [config_response, subnets_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        self.assertIn("ddns", client.modules)

    @patch("kea_exporter.http.requests.post")
    def test_load_modules_fallback_d2_normalized(self, mock_post):
        """Test that d2 is normalized to ddns"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"d2": {}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = []

        mock_post.side_effect = [config_response, subnets_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        self.assertIn("ddns", client.modules)
        self.assertNotIn("d2", client.modules)

    @patch("kea_exporter.http.requests.post")
    def test_load_modules_case_insensitive(self, mock_post):
        """Test that module detection is case-insensitive"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"DHCP4": {}, "DDNS": {}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = [{"arguments": {"Dhcp4": {"subnet4": []}}}]

        mock_post.side_effect = [config_response, subnets_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        self.assertIn("dhcp4", client.modules)
        self.assertIn("ddns", client.modules)

    @patch("kea_exporter.http.requests.post")
    def test_load_modules_uses_timeout(self, mock_post):
        """Test that load_modules uses configured timeout"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {}}]

        subnets_response = Mock()
        subnets_response.json.return_value = []

        mock_post.side_effect = [config_response, subnets_response]

        KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None, timeout=25)

        # Check that timeout was passed to requests.post
        call_args = mock_post.call_args_list[0]
        self.assertEqual(call_args[1]["timeout"], 25)

    @patch("kea_exporter.http.requests.post")
    def test_load_modules_uses_auth(self, mock_post):
        """Test that load_modules uses authentication"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {}}]

        subnets_response = Mock()
        subnets_response.json.return_value = []

        mock_post.side_effect = [config_response, subnets_response]

        KeaHTTPClient(target="http://user:pass@localhost:8000", client_cert=None, client_key=None)

        # Check that auth was passed to requests.post
        call_args = mock_post.call_args_list[0]
        self.assertEqual(call_args[1]["auth"], ("user", "pass"))


class TestKeaHTTPClientLoadSubnets(unittest.TestCase):
    """Test KeaHTTPClient.load_subnets method"""

    @patch("kea_exporter.http.requests.post")
    def test_load_subnets_dhcp4(self, mock_post):
        """Test loading DHCPv4 subnets"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"dhcp4": {}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = [
            {
                "arguments": {
                    "Dhcp4": {"subnet4": [{"id": 1, "subnet": "192.168.1.0/24"}, {"id": 2, "subnet": "192.168.2.0/24"}]}
                }
            }
        ]

        mock_post.side_effect = [config_response, subnets_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        self.assertEqual(len(client.subnets), 2)
        self.assertIn(1, client.subnets)
        self.assertIn(2, client.subnets)
        self.assertEqual(client.subnets[1]["subnet"], "192.168.1.0/24")

    @patch("kea_exporter.http.requests.post")
    def test_load_subnets_dhcp6(self, mock_post):
        """Test loading DHCPv6 subnets"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"dhcp6": {}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = [
            {
                "arguments": {
                    "Dhcp6": {
                        "subnet6": [{"id": 10, "subnet": "2001:db8::/64"}, {"id": 11, "subnet": "2001:db8:1::/64"}]
                    }
                }
            }
        ]

        mock_post.side_effect = [config_response, subnets_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        self.assertEqual(len(client.subnets6), 2)
        self.assertIn(10, client.subnets6)
        self.assertIn(11, client.subnets6)

    @patch("kea_exporter.http.requests.post")
    def test_load_subnets_ddns_only_no_request(self, mock_post):
        """Test that load_subnets doesn't make request for DDNS-only"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"ddns": {}}}]

        mock_post.side_effect = [config_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        # Only one POST call should have been made (for config-get,
        # not subnets)
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(len(client.subnets), 0)
        self.assertEqual(len(client.subnets6), 0)


class TestKeaHTTPClientStats(unittest.TestCase):
    """Test KeaHTTPClient.stats method"""

    @patch("kea_exporter.http.requests.post")
    def test_stats_dhcp4(self, mock_post):
        """Test stats retrieval for DHCP4"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"dhcp4": {}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = [{"arguments": {"Dhcp4": {"subnet4": [{"id": 1}]}}}]

        stats_response = Mock()
        stats_response.json.return_value = [
            {"arguments": {"pkt4-received": [[100, "2024-01-01"]], "pkt4-ack-sent": [[50, "2024-01-01"]]}}
        ]

        mock_post.side_effect = [config_response, subnets_response, subnets_response, stats_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        results = list(client.stats())

        self.assertEqual(len(results), 1)
        server_id, dhcp_version, arguments, _subnets = results[0]
        self.assertEqual(server_id, "http://localhost:8000")
        self.assertEqual(dhcp_version, DHCPVersion.DHCP4)
        self.assertIn("pkt4-received", arguments)

    @patch("kea_exporter.http.requests.post")
    def test_stats_dhcp6(self, mock_post):
        """Test stats retrieval for DHCP6"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"dhcp6": {}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = [{"arguments": {"Dhcp6": {"subnet6": []}}}]

        stats_response = Mock()
        stats_response.json.return_value = [{"arguments": {"pkt6-received": [[200, "2024-01-01"]]}}]

        mock_post.side_effect = [config_response, subnets_response, subnets_response, stats_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        results = list(client.stats())

        self.assertEqual(len(results), 1)
        _server_id, dhcp_version, _arguments, _subnets = results[0]
        self.assertEqual(dhcp_version, DHCPVersion.DHCP6)

    @patch("kea_exporter.http.requests.post")
    def test_stats_ddns(self, mock_post):
        """Test stats retrieval for DDNS"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"ddns": {}}}]

        stats_response = Mock()
        stats_response.json.return_value = [
            {"arguments": {"ncr-received": [[150, "2024-01-01"]], "update-sent": [[100, "2024-01-01"]]}}
        ]

        mock_post.side_effect = [config_response, stats_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        results = list(client.stats())

        self.assertEqual(len(results), 1)
        _server_id, dhcp_version, _arguments, subnets = results[0]
        self.assertEqual(dhcp_version, DHCPVersion.DDNS)
        self.assertEqual(subnets, {})  # DDNS has no subnets

    @patch("kea_exporter.http.requests.post")
    def test_stats_multiple_modules(self, mock_post):
        """Test stats retrieval for multiple modules"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"dhcp4": {}, "ddns": {}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = [{"arguments": {"Dhcp4": {"subnet4": []}}}]

        stats_response = Mock()
        stats_response.json.return_value = [
            {"arguments": {"pkt4-received": [[100, "2024-01-01"]]}},
            {"arguments": {"ncr-received": [[50, "2024-01-01"]]}},
        ]

        mock_post.side_effect = [config_response, subnets_response, subnets_response, stats_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None)

        results = list(client.stats())

        self.assertEqual(len(results), 2)
        # Check both DHCP4 and DDNS results
        dhcp_versions = [r[1] for r in results]
        self.assertIn(DHCPVersion.DHCP4, dhcp_versions)
        self.assertIn(DHCPVersion.DDNS, dhcp_versions)

    @patch("kea_exporter.http.requests.post")
    def test_stats_server_id_without_credentials(self, mock_post):
        """Test that server_id doesn't include credentials"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"dhcp4": {}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = [{"arguments": {"Dhcp4": {"subnet4": []}}}]

        stats_response = Mock()
        stats_response.json.return_value = [{"arguments": {}}]

        mock_post.side_effect = [config_response, subnets_response, subnets_response, stats_response]

        client = KeaHTTPClient(target="http://admin:secret@localhost:8000", client_cert=None, client_key=None)

        results = list(client.stats())

        server_id = results[0][0]
        # Ensure credentials are NOT in server_id
        self.assertEqual(server_id, "http://localhost:8000")
        self.assertNotIn("admin", server_id)
        self.assertNotIn("secret", server_id)

    @patch("kea_exporter.http.requests.post")
    def test_stats_uses_timeout(self, mock_post):
        """Test that stats uses configured timeout"""
        config_response = Mock()
        config_response.json.return_value = [{"arguments": {"dhcp4": {}}}]

        subnets_response = Mock()
        subnets_response.json.return_value = [{"arguments": {"Dhcp4": {"subnet4": []}}}]

        stats_response = Mock()
        stats_response.json.return_value = [{"arguments": {}}]

        mock_post.side_effect = [config_response, subnets_response, subnets_response, stats_response]

        client = KeaHTTPClient(target="http://localhost:8000", client_cert=None, client_key=None, timeout=20)

        list(client.stats())

        # Check stats call used timeout
        stats_call = mock_post.call_args_list[-1]
        self.assertEqual(stats_call[1]["timeout"], 20)


class TestURLParsing(unittest.TestCase):
    """Test URL parsing edge cases"""

    @patch("kea_exporter.http.requests.post")
    def test_url_with_special_chars_in_password(self, mock_post):
        """Test URL with special characters in password"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {}}]
        mock_post.return_value = mock_response

        # Password contains special chars that need URL encoding
        client = KeaHTTPClient(target="http://user:p%40ssw0rd@localhost:8000", client_cert=None, client_key=None)

        self.assertEqual(client._auth, ("user", "p@ssw0rd"))
        self.assertEqual(client._target, "http://localhost:8000")

    @patch("kea_exporter.http.requests.post")
    def test_url_with_query_params(self, mock_post):
        """Test URL with query parameters"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {}}]
        mock_post.return_value = mock_response

        client = KeaHTTPClient(
            target="http://user:pass@localhost:8000/api?param=value", client_cert=None, client_key=None
        )

        self.assertIn("?param=value", client._target)
        self.assertEqual(client._auth, ("user", "pass"))

    @patch("kea_exporter.http.requests.post")
    def test_https_url(self, mock_post):
        """Test HTTPS URL handling"""
        mock_response = Mock()
        mock_response.json.return_value = [{"arguments": {}}]
        mock_post.return_value = mock_response

        client = KeaHTTPClient(target="https://user:pass@secure.example.com:8443", client_cert=None, client_key=None)

        self.assertEqual(client._target, "https://secure.example.com:8443")
        self.assertTrue(client._target.startswith("https://"))


if __name__ == "__main__":
    unittest.main()
