from __future__ import annotations

import sys
from typing import Any
from urllib.parse import unquote, urlparse, urlunparse

import requests

from kea_exporter import DHCPVersion


class KeaHTTPClient:
    def __init__(
        self,
        target: str,
        client_cert: str | None = None,
        client_key: str | None = None,
        timeout: int = 10,
        **_kwargs: Any,
    ) -> None:
        # kwargs allows passing additional arguments from CLI without breaking
        # this class
        """
        Create a KeaHTTPClient configured to communicate with a Kea server
        and initialize its module and subnet caches.

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
            **kwargs: Intentionally unused; allows Exporter to pass same
                arguments to both KeaHTTPClient and KeaSocketClient (e.g.,
                timeout, client_cert) without errors.

        Notes:
            Initializes internal state (modules, subnet maps) and triggers
            discovery of available modules and subnets.
        """
        super().__init__()

        # Parse URL to extract credentials
        parsed = urlparse(target)

        # Extract basic auth from URL if present
        if parsed.username and parsed.password:
            self._auth = (unquote(parsed.username), unquote(parsed.password))
            # Remove credentials from URL for actual requests and server ID
            netloc_without_auth = parsed.hostname
            if parsed.port:
                netloc_without_auth = f"{netloc_without_auth}:{parsed.port}"
            self._target = urlunparse(
                (parsed.scheme, netloc_without_auth, parsed.path, parsed.params, parsed.query, parsed.fragment)
            )
            # Use clean URL (without credentials) as server identifier
            self._server_id = self._target
        else:
            self._target = target
            self._server_id = target
            self._auth = None

        if client_cert and client_key:
            self._cert = (
                client_cert,
                client_key,
            )
        else:
            self._cert = None

        self.timeout = timeout
        self.modules = []
        self.subnets = {}
        self.subnets6 = {}

        self.load_modules()
        self.load_subnets()

    def load_modules(self):
        """
        Discover available Kea services and populate self.modules.

        Queries the server configuration and fills self.modules with the
        detected service names. Prefers discovery via the Control-agent's
        `control-sockets` entry when present; otherwise inspects top-level
        service keys case-insensitively and adds any of "dhcp4", "dhcp6",
        or "ddns" as found. Treats the legacy key "d2" as "ddns".
        """
        r = requests.post(
            self._target,
            cert=self._cert,
            auth=self._auth,
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
        control_sockets = config_args.get("Control-agent", {}).get("control-sockets", [])

        # control-sockets can be either a list (legacy) or a dict
        # (proper Kea API)
        if isinstance(control_sockets, dict):
            # Extract service names from dict keys
            modules = list(control_sockets.keys())
        else:
            # Use list as-is
            modules = control_sockets

        # Set modules if discovery succeeded
        if modules:
            self.modules = modules
        else:
            # Fallback for setups without Control Agent (Kea 2.7.2+ and
            # newer may not have it)
            # Normalize keys to lowercase for case-insensitive detection
            lower_args = {k.lower(): v for k, v in config_args.items()}
            for service in ["dhcp4", "dhcp6", "ddns", "d2"]:
                if service in lower_args:
                    # Normalize d2 to ddns
                    if service == "d2":
                        self.modules.append("ddns")
                    else:
                        self.modules.append(service)

    def load_subnets(self):
        # Only load subnets for DHCP services (DDNS doesn't have subnets)
        """
        Load IPv4 and IPv6 subnet definitions for configured DHCP modules
        into the instance maps.

        Fetches configuration for any DHCP modules present in self.modules
        (only "dhcp4" and "dhcp6" are considered) and replaces self.subnets
        with IPv4 subnet entries keyed by their `id` and self.subnets6 with
        IPv6 subnet entries keyed by their `id`. Includes subnets from both
        top-level configuration and shared-networks. If no DHCP modules are
        configured, the method returns without modifying state.
        """
        dhcp_modules = [m for m in self.modules if m in ["dhcp4", "dhcp6"]]
        if not dhcp_modules:
            return

        r = requests.post(
            self._target,
            cert=self._cert,
            auth=self._auth,
            json={"command": "config-get", "service": dhcp_modules},
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        config = r.json()

        new_subnets = {}
        new_subnets6 = {}
        dhcp4_seen = False
        dhcp6_seen = False
        for module in config:
            # Skip non-dict responses (e.g., error strings)
            if not isinstance(module, dict):
                continue
            # Skip Kea-level error responses
            if module.get("result", 0) != 0:
                continue
            args = module.get("arguments", {})

            dhcp4_config = args.get("Dhcp4", {})
            if "Dhcp4" in args:
                dhcp4_seen = True
            for subnet in dhcp4_config.get("subnet4", []):
                new_subnets[subnet["id"]] = subnet
            for network in dhcp4_config.get("shared-networks", []):
                for subnet in network.get("subnet4", []):
                    new_subnets[subnet["id"]] = subnet

            dhcp6_config = args.get("Dhcp6", {})
            if "Dhcp6" in args:
                dhcp6_seen = True
            for subnet in dhcp6_config.get("subnet6", []):
                new_subnets6[subnet["id"]] = subnet
            for network in dhcp6_config.get("shared-networks", []):
                for subnet in network.get("subnet6", []):
                    new_subnets6[subnet["id"]] = subnet

        if dhcp4_seen:
            self.subnets = new_subnets
        if dhcp6_seen:
            self.subnets6 = new_subnets6

    def stats(self):
        # Reload subnets on update in case of configurational update
        """
        Fetch statistics from the Kea server and yield a record for each
        discovered module.

        Each yielded record corresponds to a module in the client's
        discovered module list and contains the server identifier, the
        module's DHCP version, the module-specific statistics/arguments,
        and the relevant subnet mapping. For the DDNS module the subnet
        mapping is an empty dict.

        Returns:
            iterator: Yields tuples of the form (server_id, dhcp_version,
                arguments, subnets) where
                - server_id (str): identifier for the Kea server (clean
                  target URL),
                - dhcp_version (DHCPVersion): enum value indicating DHCP4,
                  DHCP6, or DDNS,
                - arguments (dict): statistics/arguments returned by the
                  module,
                - subnets (dict): mapping of subnet id to subnet definition
                  (empty for DDNS).
        """
        # Reload subnets on every scrape to pick up runtime config changes
        # (e.g. subnets added/removed via config-set). This costs one extra
        # HTTP request per scrape but avoids stale subnet labels.
        # Best-effort: don't abort the scrape if subnet refresh fails.
        try:
            self.load_subnets()
        except Exception as e:
            print(
                f"Warning: failed to refresh subnets for {self._server_id}, using cached data: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
        r = requests.post(
            self._target,
            cert=self._cert,
            auth=self._auth,
            json={
                "command": "statistic-get-all",
                "arguments": {},
                "service": self.modules,
            },
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        response = r.json()

        for index, module in enumerate(self.modules):
            if module == "dhcp4":
                dhcp_version = DHCPVersion.DHCP4
                subnets = self.subnets
            elif module == "dhcp6":
                dhcp_version = DHCPVersion.DHCP6
                subnets = self.subnets6
            elif module == "ddns":
                dhcp_version = DHCPVersion.DDNS
                subnets = {}  # DDNS doesn't have subnets
            else:
                continue

            entry = response[index]
            # Validate per-module entry shape
            if not isinstance(entry, dict) or "result" not in entry:
                raise ValueError(f"Kea statistic-get-all returned malformed entry for module {module!r}: {entry!r}")
            # Skip modules where Kea reported an error
            if entry["result"] != 0:
                continue
            arguments = entry.get("arguments", {})

            yield self._server_id, dhcp_version, arguments, subnets
