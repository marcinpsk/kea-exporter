"""Subnet discovery through real HTTP and Unix socket transports."""

import socket
import threading

import pytest

from kea_exporter import DHCPVersion
from kea_exporter.http import KeaHTTPClient
from kea_exporter.subnets import subnet_index
from kea_exporter.uds import KeaSocketClient
from tests.support import KeaHTTPServer, KeaUnixSocketServer, _KeaUnixSocketHandler


@pytest.fixture
def no_proxy(monkeypatch):
    """Keep an ambient proxy configuration out of loopback tests."""
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


@pytest.fixture
def kea_http_server(no_proxy):
    """Start HTTP fakes and close them after each test."""
    servers = []

    def start(config_get, statistic_get_all):
        server = KeaHTTPServer(config_get, statistic_get_all)
        servers.append(server)
        return server

    yield start

    for server in servers:
        server.close()


@pytest.fixture
def kea_unix_socket_server(tmp_path):
    """Start Unix socket fakes and close them after each test."""
    servers = []

    def start(config_get, statistic_get_all):
        path = tmp_path / f"kea-{len(servers)}.sock"
        server = KeaUnixSocketServer(path, config_get, statistic_get_all)
        servers.append(server)
        return server

    yield start

    for server in servers:
        server.close()


DAEMON_CASES = [
    pytest.param(
        DHCPVersion.DHCP4,
        "Dhcp4",
        "subnet4",
        [
            {"id": 1, "subnet": "198.18.1.0/24"},
            {"id": 2, "subnet": "198.18.2.0/24"},
            {"id": 3, "subnet": "198.18.3.0/24"},
        ],
        id="dhcp4",
    ),
    pytest.param(
        DHCPVersion.DHCP6,
        "Dhcp6",
        "subnet6",
        [
            {"id": 1, "subnet": "2001:db8:1::/64"},
            {"id": 2, "subnet": "2001:db8:2::/64"},
            {"id": 3, "subnet": "2001:db8:3::/64"},
        ],
        id="dhcp6",
    ),
]


def config_section(subnet_key, subnets):
    """Put one subnet at the top level and one in each shared network."""
    return {
        subnet_key: [subnets[0]],
        "shared-networks": [
            {subnet_key: [subnets[1]]},
            {subnet_key: [subnets[2]]},
        ],
    }


@pytest.mark.parametrize(("dhcp_version", "section_name", "subnet_key", "subnets"), DAEMON_CASES)
def test_the_http_client_collects_daemon_subnets_from_the_top_level_and_every_shared_network(
    kea_http_server, dhcp_version, section_name, subnet_key, subnets
):
    config_get = [{"result": 0, "arguments": {section_name: config_section(subnet_key, subnets)}}]
    statistic_get_all = [{"result": 0, "arguments": {}}]
    server = kea_http_server(config_get, statistic_get_all)

    rows = list(KeaHTTPClient(server.target).stats())

    assert len(rows) == 1
    assert rows[0][1] is dhcp_version
    assert rows[0][3] == {subnet["id"]: subnet for subnet in subnets}


def test_an_empty_dhcp4_section_replaces_the_http_clients_previously_loaded_subnets(kea_http_server):
    subnet = {"id": 1, "subnet": "198.18.1.0/24"}
    initial_config = [{"result": 0, "arguments": {"Dhcp4": {"subnet4": [subnet]}}}]
    statistic_get_all = [{"result": 0, "arguments": {}}]
    server = kea_http_server(initial_config, statistic_get_all)
    client = KeaHTTPClient(server.target)

    first_rows = list(client.stats())
    server.responses["config-get"] = [{"result": 0, "arguments": {"Dhcp4": {"subnet4": []}}}]
    second_rows = list(client.stats())

    assert first_rows[0][3] == {1: subnet}
    assert second_rows[0][3] == {}


def test_a_response_without_a_dhcp4_section_keeps_the_http_clients_previously_loaded_subnets(kea_http_server):
    subnet = {"id": 1, "subnet": "198.18.1.0/24"}
    initial_config = [{"result": 0, "arguments": {"Dhcp4": {"subnet4": [subnet]}}}]
    statistic_get_all = [{"result": 0, "arguments": {}}]
    server = kea_http_server(initial_config, statistic_get_all)
    client = KeaHTTPClient(server.target)

    first_rows = list(client.stats())
    server.responses["config-get"] = [{"result": 0, "arguments": {}}]
    second_rows = list(client.stats())

    assert first_rows[0][3] == {1: subnet}
    assert second_rows[0][3] == {1: subnet}


@pytest.mark.parametrize(("dhcp_version", "section_name", "subnet_key", "subnets"), DAEMON_CASES)
def test_the_unix_socket_client_collects_daemon_subnets_from_the_top_level_and_every_shared_network(
    kea_unix_socket_server, dhcp_version, section_name, subnet_key, subnets
):
    config_get = {"result": 0, "arguments": {section_name: config_section(subnet_key, subnets)}}
    statistic_get_all = {"result": 0, "arguments": {}}
    server = kea_unix_socket_server(config_get, statistic_get_all)

    rows = list(KeaSocketClient(server.path).stats())

    assert len(rows) == 1
    assert rows[0][1] is dhcp_version
    assert rows[0][3] == {subnet["id"]: subnet for subnet in subnets}


def test_the_unix_socket_handler_stops_when_a_peer_closes_before_sending_valid_json():
    server_socket, peer_socket = socket.socketpair()
    errors = []

    def handle():
        try:
            _KeaUnixSocketHandler(server_socket, None, None)
        except OSError as error:
            errors.append(error)

    thread = threading.Thread(target=handle)
    thread.start()
    peer_socket.close()
    # Generous: the fixed handler returns at once, the busy-loop never returns.
    thread.join(timeout=5)
    was_alive = thread.is_alive()
    server_socket.close()
    thread.join(timeout=1)

    assert not was_alive
    assert errors == []


def test_subnet_index_skips_every_subnet_that_has_no_id():
    top_level = {"id": 1, "subnet": "198.18.1.0/24"}
    shared = {"id": 2, "subnet": "198.18.2.0/24"}
    section = {
        "subnet4": [{"subnet": "198.18.10.0/24"}, top_level],
        "shared-networks": [{"subnet4": [{"subnet": "198.18.20.0/24"}, shared]}],
    }

    assert subnet_index(section, "subnet4") == {1: top_level, 2: shared}


def test_subnet_index_returns_an_empty_map_for_an_empty_section():
    assert subnet_index({}, "subnet4") == {}


def test_subnet_index_collects_shared_network_subnets_when_the_top_level_list_is_absent():
    subnet = {"id": 1, "subnet": "2001:db8:1::/64"}
    section = {"shared-networks": [{"subnet6": [subnet]}]}

    assert subnet_index(section, "subnet6") == {1: subnet}
