"""Collect Kea statistics through its HTTP control API."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse, urlunparse

import click
import requests

from kea_exporter import DHCPVersion
from kea_exporter.daemon import DAEMON_SPECS, daemon_for_service
from kea_exporter.subnets import SubnetIndex, subnet_index


class KeaHTTPClient:
    def __init__(
        self,
        target: str,
        client_cert: str | None = None,
        client_key: str | None = None,
        timeout: int = 10,
        tls_no_verify: bool = False,
        ca_bundle: str | None = None,
        **_kwargs: Any,
    ) -> None:
        # kwargs allows passing additional arguments from CLI without breaking
        # this class
        """
        Create a KeaHTTPClient configured to communicate with a Kea server.
        Construction performs no I/O.

        Parameters:
            target (str): Kea server URL; may include embedded credentials
                (e.g. "https://user:pass@host:port"). Embedded credentials
                will be used for HTTP basic auth and removed from the
                effective request URL.
            client_cert (str | None): Path to the client TLS certificate
                file, or None to disable mutual TLS.
            client_key (str | None): Path to the client TLS key file, or
                None to disable mutual TLS.
            timeout (int | float): HTTP request timeout in seconds
                (default 10).
            tls_no_verify (bool): If True, disable TLS certificate verification
                (insecure; default False).
            ca_bundle (str | None): Path to a CA bundle file for TLS
                verification, or None to use system defaults.
            **kwargs: Intentionally unused; allows Exporter to pass same
                arguments to both KeaHTTPClient and KeaSocketClient (e.g.,
                timeout, client_cert) without errors.

        Notes:
            The first stats() call discovers available daemons and subnets.
        """
        super().__init__()

        # Parse URL to extract credentials
        parsed = urlparse(target)

        # Any userinfo comes out of the URL: the server id below becomes the
        # `server` label on every series. Test the delimiter rather than the
        # parts, because urlparse reports an empty username for both
        # "http://:secret@host" and "http://@host".
        if "@" in parsed.netloc:
            # requests derives no auth from a username with no password, so
            # neither do we, or stripping the URL would start sending one.
            self._auth = (
                (unquote(parsed.username or ""), unquote(parsed.password)) if parsed.password is not None else None
            )
            # Cut the userinfo off the netloc rather than rebuilding it from
            # parsed.hostname, which drops the brackets from an IPv6 host and
            # leaves a URL requests refuses to parse.
            netloc_without_auth = parsed.netloc.rpartition("@")[2]
            self._target = urlunparse(
                (parsed.scheme, netloc_without_auth, parsed.path, parsed.params, parsed.query, parsed.fragment)
            )
            # Use clean URL (without credentials) as server identifier
            self._server_id = self._target
        else:
            self._target = target
            self._server_id = target
            self._auth = None

        if client_cert or client_key:
            if not (client_cert and client_key):
                raise ValueError("Both client_cert and client_key must be provided for mutual TLS")
            self._cert = (client_cert, client_key)
        else:
            self._cert = None

        self.timeout = timeout
        if tls_no_verify:
            if ca_bundle:
                click.echo(
                    "Warning: --no-tls-verify takes precedence over --ca-bundle; TLS verification is disabled.",
                    err=True,
                )
            self._verify: bool | str = False
        elif ca_bundle is not None:
            self._verify = ca_bundle
        else:
            self._verify = True
        self._subnets_by_daemon: dict[DHCPVersion, SubnetIndex] = {}
        self._discovered = False
        self._through_control_agent = False
        self._subnet_refresh_failed = False

    @property
    def server_id(self) -> str:
        return self._server_id

    def load_modules(self):
        """
        Discover available Kea services and populate the daemon map.

        Queries the server configuration and records the detected daemons.
        Prefers discovery via the Control-agent's
        `control-sockets` entry when present; otherwise matches the top-level
        direct-daemon configuration sections case-insensitively.
        """
        r = requests.post(
            self._target,
            cert=self._cert,
            auth=self._auth,
            verify=self._verify,
            json={"command": "config-get"},
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        config = r.json()

        # Validate Kea RPC response shape before accessing payload
        if not isinstance(config, list) or not config or not isinstance(config[0], dict) or "result" not in config[0]:
            raise ValueError(f"Kea config-get returned malformed response: {config!r}")
        if config[0]["result"] != 0:
            error_text = config[0].get("text") or f"result={config[0]['result']}"
            raise ValueError(f"Kea config-get failed: {error_text}")

        config_args = config[0].get("arguments", {})

        # Try Control Agent discovery first (legacy)
        control_agent = config_args.get("Control-agent")
        self._through_control_agent = isinstance(control_agent, dict)
        control_sockets = control_agent.get("control-sockets", []) if self._through_control_agent else []

        # control-sockets can be either a list (legacy) or a dict
        # (proper Kea API)
        if isinstance(control_sockets, dict):
            # Extract service names from dict keys
            services = list(control_sockets.keys())
        else:
            # Use list as-is
            services = control_sockets

        if services:
            daemons = []
            for service in services:
                if not isinstance(service, str):
                    continue
                daemon = daemon_for_service(service)
                if daemon is not None and daemon not in daemons:
                    daemons.append(daemon)
        else:
            # Direct daemon endpoints return their configuration section.
            lower_args = {k.lower(): v for k, v in config_args.items()}
            daemons = [
                daemon
                for daemon, spec in DAEMON_SPECS.items()
                if spec.section is not None and spec.section.lower() in lower_args
            ]

        self._subnets_by_daemon = {daemon: self._subnets_by_daemon.get(daemon, {}) for daemon in daemons}

    def _command(self, name, daemons, arguments=None):
        """Build a command for a direct daemon or the legacy Control Agent."""
        command = {"command": name}
        if arguments is not None:
            command["arguments"] = arguments
        if self._through_control_agent:
            command["service"] = [DAEMON_SPECS[daemon].service for daemon in daemons]
        return command

    def load_subnets(self):
        """
        Load subnet definitions for every discovered DHCP daemon.

        Replaces the subnet index when Kea returns that daemon's section.
        A missing section keeps the last successful index.
        """
        dhcp_daemons = [daemon for daemon in self._subnets_by_daemon if DAEMON_SPECS[daemon].subnet_key is not None]
        if not dhcp_daemons:
            return

        r = requests.post(
            self._target,
            cert=self._cert,
            auth=self._auth,
            verify=self._verify,
            json=self._command("config-get", dhcp_daemons),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        config = r.json()

        indexed_by_daemon: dict[DHCPVersion, SubnetIndex] = {}
        for entry in config:
            if not isinstance(entry, dict):
                continue
            if "result" not in entry:
                raise ValueError(f"Kea config-get returned malformed subnet entry: {entry!r}")
            if entry["result"] != 0:
                continue
            args = entry.get("arguments", {})

            for daemon in dhcp_daemons:
                spec = DAEMON_SPECS[daemon]
                if spec.section is not None and spec.subnet_key is not None and spec.section in args:
                    indexed_by_daemon.setdefault(daemon, {}).update(subnet_index(args[spec.section], spec.subnet_key))

        for daemon, subnets in indexed_by_daemon.items():
            self._subnets_by_daemon[daemon] = subnets

    def _report_subnet_refresh_failure(self, ex: Exception) -> None:
        """Report the first failed subnet refresh in an outage."""
        if self._subnet_refresh_failed:
            return
        self._subnet_refresh_failed = True
        click.echo(
            f"Warning: failed to refresh subnets for {self._server_id}, using cached data: {type(ex).__name__}: {ex}",
            err=True,
        )

    def _report_subnet_refresh_recovery(self) -> None:
        """Close the report opened by _report_subnet_refresh_failure."""
        if not self._subnet_refresh_failed:
            return
        self._subnet_refresh_failed = False
        click.echo(f"Refreshed subnets for {self._server_id} again", err=True)

    def stats(self):
        # Reload subnets on update in case of configurational update
        """
        Fetch statistics from the Kea server and yield a record for each
        discovered daemon.

        Each yielded record corresponds to one discovered daemon and contains
        the server identifier, the daemon identity, the daemon statistics,
        and the relevant subnet mapping. For the DDNS daemon the subnet
        mapping is an empty dict.

        Returns:
            iterator: Yields tuples of the form (server_id, dhcp_version,
                arguments, subnets) where
                - server_id (str): identifier for the Kea server (clean
                  target URL),
                - dhcp_version (DHCPVersion): enum value indicating DHCP4,
                  DHCP6, or DDNS,
                - arguments (dict): statistics/arguments returned by the
                  daemon,
                - subnets (dict): mapping of subnet id to subnet definition
                  (empty for DDNS).
        """
        if not self._discovered:
            # First scrape: nothing can be read until the daemons are known, so
            # a failure here belongs to the caller.
            self.load_modules()
            self.load_subnets()
            self._discovered = True
        else:
            # Reload subnets on every scrape to pick up runtime config changes
            # (e.g. subnets added/removed via config-set). This costs one extra
            # HTTP request per scrape but avoids stale subnet labels.
            # Best-effort: don't abort the scrape if subnet refresh fails.
            try:
                self.load_subnets()
            except Exception as e:
                self._report_subnet_refresh_failure(e)
            else:
                self._report_subnet_refresh_recovery()
        r = requests.post(
            self._target,
            cert=self._cert,
            auth=self._auth,
            verify=self._verify,
            json=self._command("statistic-get-all", self._subnets_by_daemon, arguments={}),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        response = r.json()

        for index, (daemon, subnets) in enumerate(self._subnets_by_daemon.items()):
            service = DAEMON_SPECS[daemon].service
            if index >= len(response):
                raise ValueError(
                    f"Kea statistic-get-all response is missing entry for daemon {service!r} at index {index}"
                )
            entry = response[index]
            # Validate each daemon entry before reading it.
            if not isinstance(entry, dict) or "result" not in entry:
                raise ValueError(f"Kea statistic-get-all returned malformed entry for daemon {service!r}: {entry!r}")
            # Skip daemons where Kea reported an error
            if entry["result"] != 0:
                continue
            arguments = entry.get("arguments", {})

            yield self._server_id, daemon, arguments, subnets
