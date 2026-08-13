"""Collect Kea statistics through a Unix domain socket."""

import json
import os
import socket

from kea_exporter.daemon import DAEMON_SPECS
from kea_exporter.subnets import subnet_index


class KeaConfigError(Exception):
    pass


class KeaSocketClient:
    def __init__(self, sock_path: str, **kwargs) -> None:
        # **kwargs intentionally unused; allows Exporter to pass same
        # arguments to both KeaHTTPClient and KeaSocketClient (e.g.,
        # timeout, client_cert) without errors
        """
        Record the Unix domain socket path without accessing it.

        Parameters:
            sock_path (str): Path to the Unix domain socket used to
                communicate with the Kea server.

        Description:
            Stores the absolute socket path and initializes internal state
            (version, config, subnets, subnet_missing_info_sent,
            dhcp_version). The absolute socket path is also recorded as the
            client/server identifier.
        """
        super().__init__()

        self.sock_path = os.path.abspath(sock_path)
        # Use socket path as server identifier
        self._server_id = self.sock_path
        self.timeout = kwargs.get("timeout", 10)

        self.version = None
        self.config = None
        self.subnets = None
        self.subnet_missing_info_sent = set()
        self.dhcp_version = None

    @property
    def server_id(self) -> str:
        return self._server_id

    def _check_socket(self):
        """Fail with the reason rather than letting connect() report a bare error.

        Checked per scrape, not once at construction: Kea may create the socket
        after the exporter starts, and the scrape loop retries.

        Raises:
            FileNotFoundError: If no socket exists at the recorded path.
            PermissionError: If the socket is not writable by the current
                process.
        """
        if not os.access(self.sock_path, os.F_OK):
            raise FileNotFoundError(f"Unix domain socket does not exist at {self.sock_path}")
        if not os.access(self.sock_path, os.W_OK):
            raise PermissionError(f"No write permission on Unix domain socket at {self.sock_path}")

    def query(self, command):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(self.sock_path)
            sock.sendall(json.dumps({"command": command}).encode())
            # Use recv() loop so the socket timeout applies to every read,
            # preventing a permanent hang if Kea stops sending before EOF.
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)

        try:
            response = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Kea returned invalid JSON on '{command}': {e}") from e

        if response["result"] != 0:
            raise ValueError(response.get("text") or f"Query '{command}' failed with result {response['result']}")

        return response

    def stats(self):
        # I don't currently know how to detect a changed configuration, so
        # unfortunately we're reloading more often now as a workaround.
        """
        Yield current server statistics and subnet mapping after
        reloading the configuration.

        Yields:
            tuple: (server_id, dhcp_version, arguments, subnets)
                - server_id (str): Unix domain socket path.
                - dhcp_version (DHCPVersion): Detected DHCP version.
                - arguments (dict): Statistics from statistic-get-all.
                - subnets (dict): Subnet ID to subnet config mapping.
        """
        self._check_socket()
        self.reload()

        arguments = self.query("statistic-get-all").get("arguments", {})

        yield self._server_id, self.dhcp_version, arguments, self.subnets

    def reload(self):
        """
        Refresh the client's configuration from the Kea server and update
        the DHCP version and subnet mapping.

        Retrieves the server configuration and stores its "arguments" in
        self.config. Selects the first supported DHCP section and populates
        self.subnets as a dictionary mapping each subnet's "id" to the subnet
        object.

        Raises:
            KeaConfigError: If neither "Dhcp4" nor "Dhcp6" is found in
                the configuration.
        """
        self.config = self.query("config-get")["arguments"]

        # Table order preserves first-match behavior, so DHCP4 wins when both sections exist.
        for dhcp_version, spec in DAEMON_SPECS.items():
            if spec.section is not None and spec.subnet_key is not None and spec.section in self.config:
                self.dhcp_version = dhcp_version
                self.subnets = subnet_index(self.config[spec.section], spec.subnet_key)
                return

        raise KeaConfigError(f"Socket {self.sock_path} has no supported configuration")
