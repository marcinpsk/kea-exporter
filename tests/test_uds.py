"""Behavior of the Unix socket target adapter through a local Kea server."""

import os
import socket

import pytest

from kea_exporter import DHCPVersion
from kea_exporter.uds import KeaConfigError, KeaSocketClient
from tests.support import KeaControl, KeaResponse, KeaUnixSocketServer

SUBNET4 = {"id": 1, "subnet": "198.18.1.0/24"}
STATISTICS4 = {"pkt4-ack-sent": [[7, "2026-01-01 00:00:00.000000"]]}
CONFIG4 = {"result": 0, "arguments": {"Dhcp4": {"subnet4": [SUBNET4]}}}
STATS4 = {"result": 0, "arguments": STATISTICS4}


@pytest.fixture
def unix_server(tmp_path):
    """Start shared Kea Unix socket adapters and close them after each test."""
    servers = []

    def start(config_get=CONFIG4, statistic_get_all=STATS4):
        path = tmp_path / f"kea-{len(servers)}.sock"
        server = KeaUnixSocketServer(path, KeaControl(config_get, statistic_get_all))
        servers.append(server)
        return server

    yield start

    for server in servers:
        server.close()


def test_a_missing_socket_is_reported_by_the_scrape(tmp_path):
    client = KeaSocketClient(tmp_path / "absent.sock")

    with pytest.raises(FileNotFoundError, match="does not exist"):
        list(client.stats())


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_a_socket_without_write_permission_is_reported_by_the_scrape(unix_server):
    server = unix_server()
    os.chmod(server.path, 0)

    with pytest.raises(PermissionError, match="No write permission"):
        list(KeaSocketClient(server.path).stats())


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_a_write_only_socket_can_be_scraped(unix_server):
    """The Unix socket pathname needs write permission, not read permission."""
    server = unix_server()
    os.chmod(server.path, 0o200)

    assert not os.access(server.path, os.R_OK)
    assert os.access(server.path, os.W_OK)
    assert list(KeaSocketClient(server.path).stats()) == [
        (server.path, DHCPVersion.DHCP4, STATISTICS4, {SUBNET4["id"]: SUBNET4})
    ]


def test_construction_records_an_absolute_path_without_performing_io(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    client = KeaSocketClient("absent.sock", timeout=30)

    assert client.server_id == os.fspath(tmp_path / "absent.sock")
    assert client.timeout == 30


def test_query_sends_the_command_and_returns_the_response(unix_server):
    server = unix_server()
    server.control.responses["version-get"] = {"result": 0, "arguments": {"version": "test"}}

    response = KeaSocketClient(server.path).query("version-get")

    assert response == {"result": 0, "arguments": {"version": "test"}}
    assert server.control.requests == [{"command": "version-get"}]


def test_query_reports_a_kea_error(unix_server):
    server = unix_server()
    server.control.responses["config-get"] = {"result": 1, "text": "configuration unavailable"}

    with pytest.raises(ValueError, match="configuration unavailable"):
        KeaSocketClient(server.path).query("config-get")


def test_query_reports_a_kea_error_without_text(unix_server):
    server = unix_server()
    server.control.responses["config-get"] = {"result": 2}

    with pytest.raises(ValueError, match="config-get.*result 2"):
        KeaSocketClient(server.path).query("config-get")


def test_query_uses_the_configured_socket_timeout(unix_server):
    server = unix_server(KeaResponse(CONFIG4, delay=0.1))

    with pytest.raises(socket.timeout):
        KeaSocketClient(server.path, timeout=0.01).query("config-get")


def test_query_reassembles_a_chunked_response(unix_server):
    chunks = (b'{"result": 0, "argu', b'ments": {"Dhcp4": {}}}')
    server = unix_server(KeaResponse(None, chunks=chunks))

    response = KeaSocketClient(server.path).query("config-get")

    assert response == {"result": 0, "arguments": {"Dhcp4": {}}}


def test_query_rejects_invalid_json(unix_server):
    server = unix_server(KeaResponse(b"not-json"))

    with pytest.raises(ValueError, match="invalid JSON.*config-get"):
        KeaSocketClient(server.path).query("config-get")


@pytest.mark.parametrize(
    ("daemon", "section", "subnet_key", "subnet", "statistics"),
    [
        (
            DHCPVersion.DHCP4,
            "Dhcp4",
            "subnet4",
            SUBNET4,
            STATISTICS4,
        ),
        (
            DHCPVersion.DHCP6,
            "Dhcp6",
            "subnet6",
            {"id": 6, "subnet": "2001:db8:6::/64"},
            {"pkt6-received": [[11, "2026-01-01 00:00:00.000000"]]},
        ),
    ],
)
def test_stats_yields_the_detected_daemons_statistics_and_subnets(
    unix_server, daemon, section, subnet_key, subnet, statistics
):
    config = {"result": 0, "arguments": {section: {subnet_key: [subnet]}}}
    server = unix_server(config, {"result": 0, "arguments": statistics})

    rows = list(KeaSocketClient(server.path).stats())

    assert rows == [(server.path, daemon, statistics, {subnet["id"]: subnet})]
    assert server.control.requests == [
        {"command": "config-get"},
        {"command": "statistic-get-all"},
    ]


def test_stats_reloads_configuration_on_every_scrape(unix_server):
    server = unix_server()
    client = KeaSocketClient(server.path)
    first_rows = list(client.stats())
    replacement = {"id": 2, "subnet": "198.18.2.0/24"}
    server.control.responses["config-get"] = {
        "result": 0,
        "arguments": {"Dhcp4": {"subnet4": [replacement]}},
    }

    second_rows = list(client.stats())

    assert first_rows[0][3] == {1: SUBNET4}
    assert second_rows[0][3] == {2: replacement}
    assert [request["command"] for request in server.control.requests].count("config-get") == 2


def test_reload_rejects_a_configuration_without_a_supported_daemon(unix_server):
    config = {"result": 0, "arguments": {"UnknownDaemon": {}}}
    server = unix_server(config)

    with pytest.raises(KeaConfigError, match="no supported configuration"):
        KeaSocketClient(server.path).reload()


def test_reload_selects_dhcp4_first_when_both_sections_are_present(unix_server):
    subnet6 = {"id": 6, "subnet": "2001:db8:6::/64"}
    config = {
        "result": 0,
        "arguments": {
            "Dhcp6": {"subnet6": [subnet6]},
            "Dhcp4": {"subnet4": [SUBNET4]},
        },
    }
    server = unix_server(config)

    row = list(KeaSocketClient(server.path).stats())[0]

    assert row[1] is DHCPVersion.DHCP4
    assert row[3] == {1: SUBNET4}
