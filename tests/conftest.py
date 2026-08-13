"""Shared pytest fixtures for real HTTP and Unix socket adapter tests."""

import pytest

from tests.support import KeaControl, KeaHTTPServer, KeaUnixSocketServer, stat

HTTP_CONFIG4 = [{"result": 0, "arguments": {"Dhcp4": {"subnet4": []}}}]
HTTP_STATISTICS4 = [{"result": 0, "arguments": {"pkt4-ack-sent": stat(7)}}]
UNIX_SUBNET4 = {"id": 1, "subnet": "198.18.1.0/24"}
UNIX_CONFIG4 = {"result": 0, "arguments": {"Dhcp4": {"subnet4": [UNIX_SUBNET4]}}}
UNIX_STATISTICS4 = {"result": 0, "arguments": {"pkt4-ack-sent": stat(7)}}


@pytest.fixture
def no_proxy(monkeypatch):
    """Keep ambient proxy configuration out of loopback tests."""
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


@pytest.fixture
def http_server(no_proxy):
    """Start local Kea HTTP servers and close them after each test."""
    servers = []

    def start(
        config_get=HTTP_CONFIG4,
        statistic_get_all=HTTP_STATISTICS4,
        server_type=KeaHTTPServer,
        host="127.0.0.1",
    ):
        server = server_type(KeaControl(config_get, statistic_get_all), host)
        servers.append(server)
        return server

    yield start

    for server in servers:
        server.close()


@pytest.fixture
def unix_server(tmp_path):
    """Start local Kea Unix socket servers and close them after each test."""
    servers = []

    def start(config_get=UNIX_CONFIG4, statistic_get_all=UNIX_STATISTICS4):
        path = tmp_path / f"kea-{len(servers)}.sock"
        server = KeaUnixSocketServer(path, KeaControl(config_get, statistic_get_all))
        servers.append(server)
        return server

    yield start

    for server in servers:
        server.close()
