"""Behavior of the HTTP target adapter through a local Kea server."""

import pytest
import requests
from prometheus_client import CollectorRegistry

from kea_exporter import DHCPVersion
from kea_exporter.exporter import Exporter
from kea_exporter.http import KeaHTTPClient
from kea_exporter.target import KeaCommandError, SourceFailure
from tests.support import KeaResponse

CONFIG4 = [{"result": 0, "arguments": {"Dhcp4": {"subnet4": []}}}]
STATISTICS4 = {"pkt4-ack-sent": [[7, "2026-01-01 00:00:00.000000"]]}
STATS4 = [{"result": 0, "arguments": STATISTICS4}]


def statistic_requests(server):
    """Return the statistic commands received by the local Kea server."""
    return [request for request in server.control.requests if request["command"] == "statistic-get-all"]


def test_a_target_recovers_when_initial_subnet_discovery_fails(http_server):
    """A repeated initial discovery must replace, not duplicate, the daemon map."""
    server = http_server()
    server.control.queue(
        "config-get",
        CONFIG4,
        KeaResponse({"error": "unavailable"}, status=503),
        CONFIG4,
    )
    registry = CollectorRegistry()
    exporter = Exporter(targets=[server.target], registry=registry)
    client = exporter.targets[0]

    with pytest.raises(requests.HTTPError):
        list(client.stats())
    exporter.update()

    assert statistic_requests(server) == [{"command": "statistic-get-all", "arguments": {}}]
    assert (
        registry.get_sample_value(
            "kea_dhcp4_packets_sent_total",
            {"server": server.target, "operation": "ack"},
        )
        == 7
    )


def test_subnet_refresh_outage_is_reported_once_and_closed_on_recovery(http_server, capsys):
    """Repeated refresh failures produce one warning until recovery."""
    server = http_server()
    client = KeaHTTPClient(server.target)
    list(client.stats())
    server.control.queue(
        "config-get",
        *(KeaResponse({"error": "unavailable"}, status=503) for _ in range(3)),
    )

    for _ in range(3):
        list(client.stats())
    list(client.stats())

    output = capsys.readouterr()
    assert output.err.count("Warning: failed to refresh subnets") == 1
    assert output.err.count(f"Refreshed subnets for {server.target} again") == 1


@pytest.mark.parametrize(
    ("subnet_response", "message"),
    [
        pytest.param([], "malformed subnet response", id="empty-response"),
        pytest.param([None], "malformed subnet entry", id="malformed-entry"),
        pytest.param(
            [{"result": 1, "text": "subnet configuration unavailable"}],
            "subnet configuration unavailable",
            id="command-error",
        ),
    ],
)
def test_initial_subnet_discovery_rejects_an_invalid_daemon_response(http_server, subnet_response, message):
    configuration = [
        {
            "result": 0,
            "arguments": {"Control-agent": {"control-sockets": {"dhcp4": {"socket-type": "unix"}}}},
        }
    ]
    server = http_server(configuration)
    server.control.queue("config-get", configuration, subnet_response)

    with pytest.raises(ValueError, match=message):
        list(KeaHTTPClient(server.target).stats())

    assert statistic_requests(server) == []


def test_construction_records_the_target_without_performing_io():
    client = KeaHTTPClient("http://127.0.0.1:1", timeout=30)

    assert client.server_id == "http://127.0.0.1:1"
    assert client.timeout == 30


@pytest.mark.parametrize(
    ("target", "server_id", "expected_auth"),
    [
        ("http://user:pass@kea.invalid", "http://kea.invalid", ("user", "pass")),
        (
            "http://user:pass@kea.invalid/control?command=yes",
            "http://kea.invalid/control?command=yes",
            ("user", "pass"),
        ),
        ("https://user:pass@kea.invalid:8443", "https://kea.invalid:8443", ("user", "pass")),
        ("http://user@kea.invalid", "http://kea.invalid", None),
        ("http://@kea.invalid", "http://kea.invalid", None),
        ("http://:secret@kea.invalid", "http://kea.invalid", ("", "secret")),
    ],
)
def test_construction_strips_userinfo_from_the_server_id(target, server_id, expected_auth):
    client = KeaHTTPClient(target)

    assert client.server_id == server_id
    assert client._auth == expected_auth


def test_discovery_reports_when_no_supported_daemon_is_configured(http_server, capsys):
    config = [
        {
            "result": 0,
            "arguments": {"Control-agent": {"control-sockets": {"unsupported": {"socket-type": "unix"}}}},
        }
    ]
    server = http_server(config, [{"result": 0, "arguments": {}}])

    assert list(KeaHTTPClient(server.target).stats()) == []

    output = capsys.readouterr()
    assert f"No supported Kea daemon was discovered at {server.target}" in output.err
    assert output.out == ""


def test_construction_decodes_userinfo_for_basic_auth():
    client = KeaHTTPClient("http://user:p%40ssword@kea.invalid")

    assert client._auth == ("user", "p@ssword")


def test_mutual_tls_requires_both_files():
    with pytest.raises(ValueError, match="Both client_cert and client_key"):
        KeaHTTPClient("https://kea.invalid", client_cert="/test/client.pem")


def test_mutual_tls_records_the_certificate_pair():
    client = KeaHTTPClient(
        "https://kea.invalid",
        client_cert="/test/client.pem",
        client_key="/test/client.key",
    )

    assert client._cert == ("/test/client.pem", "/test/client.key")


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, True),
        ({"tls_no_verify": True}, False),
        ({"ca_bundle": "/test/ca.pem"}, "/test/ca.pem"),
    ],
)
def test_construction_selects_the_requested_tls_verification(options, expected):
    assert KeaHTTPClient("https://kea.invalid", **options)._verify == expected


def test_no_verify_takes_precedence_over_a_ca_bundle(capsys):
    client = KeaHTTPClient(
        "https://kea.invalid",
        tls_no_verify=True,
        ca_bundle="/test/ca.pem",
    )

    output = capsys.readouterr()
    assert client._verify is False
    assert "TLS verification is disabled" in output.err
    assert output.out == ""


@pytest.mark.parametrize(
    ("config", "expected_daemons", "expected_services"),
    [
        (
            [
                {
                    "result": 0,
                    "arguments": {
                        "Control-agent": {
                            "control-sockets": {
                                "dhcp4": {"socket-type": "unix"},
                                "dhcp6": {"socket-type": "unix"},
                                "d2": {"socket-type": "unix"},
                            }
                        }
                    },
                }
            ],
            [DHCPVersion.DHCP4, DHCPVersion.DHCP6, DHCPVersion.DDNS],
            ["dhcp4", "dhcp6", "d2"],
        ),
        (CONFIG4, [DHCPVersion.DHCP4], None),
        ([{"result": 0, "arguments": {"Dhcp6": {"subnet6": []}}}], [DHCPVersion.DHCP6], None),
    ],
)
def test_stats_discovers_daemons_and_uses_canonical_service_names(
    http_server, config, expected_daemons, expected_services
):
    statistics = [{"result": 0, "arguments": {}} for _ in expected_daemons]
    server = http_server(config, statistics)

    rows = list(KeaHTTPClient(server.target).stats())

    assert [row[1] for row in rows] == expected_daemons
    expected_request = {"command": "statistic-get-all", "arguments": {}}
    if expected_services is not None:
        expected_request["service"] = expected_services
    assert statistic_requests(server) == [expected_request]


def test_a_direct_ddns_daemon_is_discovered_without_a_service_selector(http_server):
    """Direct Kea APIs recommend omitting the Control Agent service selector."""
    config = [{"result": 0, "arguments": {"DhcpDdns": {}}}]
    statistics = {"update-sent": [[3, "2026-01-01 00:00:00.000000"]]}
    server = http_server(config, [{"result": 0, "arguments": statistics}])

    rows = list(KeaHTTPClient(server.target).stats())

    assert rows == [(server.target, DHCPVersion.DDNS, statistics, {})]
    assert statistic_requests(server) == [{"command": "statistic-get-all", "arguments": {}}]


def test_one_daemon_map_keeps_statistics_and_subnets_aligned(http_server):
    subnet4 = {"id": 4, "subnet": "198.18.4.0/24"}
    subnet6 = {"id": 6, "subnet": "2001:db8:6::/64"}
    config = [
        {
            "result": 0,
            "arguments": {
                "Dhcp4": {"subnet4": [subnet4]},
                "Dhcp6": {"subnet6": [subnet6]},
                "DhcpDdns": {},
            },
        }
    ]
    statistics = [
        {"result": 0, "arguments": {"daemon": "four"}},
        {"result": 0, "arguments": {"daemon": "six"}},
        {"result": 0, "arguments": {"daemon": "ddns"}},
    ]
    server = http_server(config, statistics)
    server.control.queue(
        "config-get",
        [
            {
                "result": 0,
                "arguments": {
                    "Control-agent": {
                        "control-sockets": {
                            "dhcp4": {"socket-type": "unix"},
                            "dhcp6": {"socket-type": "unix"},
                            "d2": {"socket-type": "unix"},
                        }
                    }
                },
            }
        ],
    )

    rows = list(KeaHTTPClient(server.target).stats())

    assert [(row[1], row[2], row[3]) for row in rows] == [
        (DHCPVersion.DHCP4, {"daemon": "four"}, {4: subnet4}),
        (DHCPVersion.DHCP6, {"daemon": "six"}, {6: subnet6}),
        (DHCPVersion.DDNS, {"daemon": "ddns"}, {}),
    ]
    config_requests = [request for request in server.control.requests if request["command"] == "config-get"]
    assert config_requests[-1]["service"] == ["dhcp4", "dhcp6"]
    assert statistic_requests(server)[0]["service"] == ["dhcp4", "dhcp6", "d2"]


def test_ddns_does_not_request_subnet_configuration(http_server):
    config = [{"result": 0, "arguments": {"DhcpDdns": {}}}]
    server = http_server(config, [{"result": 0, "arguments": {}}])

    rows = list(KeaHTTPClient(server.target).stats())

    assert rows[0][1:] == (DHCPVersion.DDNS, {}, {})
    assert [request["command"] for request in server.control.requests].count("config-get") == 1


def test_the_configured_timeout_applies_to_http_io(http_server):
    server = http_server()
    server.control.queue("config-get", KeaResponse(CONFIG4, delay=0.1))

    with pytest.raises(requests.Timeout):
        list(KeaHTTPClient(server.target, timeout=0.01).stats())


def test_stats_reports_an_http_error_during_daemon_discovery(http_server):
    server = http_server(KeaResponse({"error": "unavailable"}, status=503))

    with pytest.raises(requests.HTTPError):
        list(KeaHTTPClient(server.target).stats())


def test_stats_raises_on_an_http_error(http_server):
    server = http_server(statistic_get_all=KeaResponse({"error": "unavailable"}, status=503))

    with pytest.raises(requests.HTTPError):
        list(KeaHTTPClient(server.target).stats())


@pytest.mark.parametrize("payload", [[], {}, [{"arguments": {}}]])
def test_stats_rejects_a_malformed_daemon_discovery_payload(http_server, payload):
    server = http_server(payload)

    with pytest.raises(ValueError, match="malformed response"):
        list(KeaHTTPClient(server.target).stats())


def test_stats_reports_a_kea_error_during_daemon_discovery(http_server):
    server = http_server([{"result": 1, "text": "config not found"}])

    with pytest.raises(ValueError, match="config not found"):
        list(KeaHTTPClient(server.target).stats())


def test_stats_rejects_a_malformed_daemon_entry(http_server):
    server = http_server(statistic_get_all=[{"arguments": {}}])

    with pytest.raises(ValueError, match="malformed entry"):
        list(KeaHTTPClient(server.target).stats())


def test_stats_rejects_a_truncated_response(http_server):
    server = http_server(statistic_get_all=[])

    with pytest.raises(ValueError, match="missing entry"):
        list(KeaHTTPClient(server.target).stats())


def test_stats_returns_a_daemon_error_to_the_exporter(http_server):
    server = http_server(statistic_get_all=[{"result": 1, "text": "unavailable"}])

    results = list(KeaHTTPClient(server.target).stats())

    assert len(results) == 1
    failure = results[0]
    assert isinstance(failure, SourceFailure)
    assert failure.server_id == server.target
    assert failure.daemon is DHCPVersion.DHCP4
    assert isinstance(failure.error, KeaCommandError)
    assert str(failure.error) == "unavailable"
