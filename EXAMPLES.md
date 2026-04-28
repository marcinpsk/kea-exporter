# Kea Exporter Usage Examples

## Multiple Endpoints with Server Labels

The exporter now supports multiple Kea endpoints with proper server labeling to distinguish metrics:

```bash
# Monitor multiple servers
kea-exporter http://dhcp-server1:8000 http://dhcp-server2:8000 /run/kea/kea.socket

# Metrics will include server labels:
# kea_dhcp4_packets_sent_total{server="http://dhcp-server1:8000", operation="ack"} 1234
# kea_dhcp4_packets_sent_total{server="http://dhcp-server2:8000", operation="ack"} 567
# kea_dhcp4_packets_sent_total{server="/run/kea/kea.socket", operation="ack"} 890
```

## Basic Authentication

### Using URL-Embedded Credentials

Credentials are embedded directly in the URL using standard `http://username:password@host:port` syntax:

```bash
# Single endpoint with auth
kea-exporter http://admin:secret@kea-server:8000

# Multiple endpoints with different credentials
kea-exporter http://admin:pass1@dhcp-server1:8000 http://monitor:pass2@dhcp-server2:8000

# Mix authenticated and non-authenticated endpoints
kea-exporter http://admin:secret@remote-server:8000 /run/kea/kea.socket
```

**Note**: Credentials are automatically stripped from the `server` label to avoid exposing passwords in metrics:

```text
# Metric label shows clean URL without credentials:
kea_dhcp4_packets_sent_total{server="http://kea-server:8000", operation="ack"} 1234
```

### Using Environment Variables

```bash
export TARGETS="http://admin:secret@kea-server:8000 http://user:pass@another-server:8001"
kea-exporter
```

## DDNS Support

The exporter now supports Kea DDNS (D2) service metrics:

```bash
# Monitor DDNS server
kea-exporter http://ddns-server:53102

# Available DDNS metrics:
# - kea_ddns_ncr_received_total{server="..."}
# - kea_ddns_update_sent_total{server="..."}
# - kea_ddns_update_success_total{server="..."}
# - kea_ddns_update_timeout_total{server="..."}
# - kea_ddns_update_error_total{server="..."}
# - kea_ddns_key_update_sent_total{server="...", key="domain.name."}
# - kea_ddns_key_update_success_total{server="...", key="domain.name."}
# - and more...
```

## Combined Example: Multiple Services with Authentication

```bash
# Monitor DHCPv4, DHCPv6, and DDNS with different credentials per endpoint
kea-exporter \
  http://dhcp-user:pass1@dhcp4-server:8000 \
  http://dhcp-user:pass2@dhcp6-server:8001 \
  http://ddns-user:pass3@ddns-server:53102 \
  --port 9547 --address 0.0.0.0

# Or using environment variables:
export TARGETS="http://user:pass@dhcp4-server:8000 http://user:pass@dhcp6-server:8001 http://user:pass@ddns-server:53102"
kea-exporter --port 9547
```

This will:
- Collect metrics from DHCPv4, DHCPv6, and DDNS servers
- Use different credentials for each endpoint (if needed)
- Expose metrics on port 9547
- Add `server` labels to distinguish between endpoints
- Credentials are stripped from labels and not exposed in metrics

## Client Certificate Authentication

For mTLS setups:

```bash
export CLIENT_CERT=/path/to/cert.pem
export CLIENT_KEY=/path/to/key.pem
export TARGETS="https://kea-server:8443"
kea-exporter
```

## TLS / HTTPS Certificate Verification

### Custom CA Bundle (internal PKI or self-signed certificates)

```bash
# Use a custom CA certificate bundle for verification
kea-exporter --ca-bundle /path/to/ca-bundle.pem https://kea-server:8443

# Or via environment variable
export CA_BUNDLE=/path/to/ca-bundle.pem
kea-exporter https://kea-server:8443
```

### Disable TLS Verification (development only — insecure)

```bash
# Skip TLS certificate verification (not recommended for production)
kea-exporter --no-tls-verify https://kea-server:8443

# Or via environment variable
export TLS_NO_VERIFY=1
kea-exporter https://kea-server:8443
```

> ⚠️ Using `--no-tls-verify` disables all TLS certificate validation. Only use this
> in isolated development/test environments.

### TLS with Client Certificate (mTLS)

Client certificates and CA bundle can be combined:

```bash
kea-exporter \
  --client-cert /etc/ssl/client.crt \
  --client-key  /etc/ssl/client.key \
  --ca-bundle   /etc/ssl/internal-ca.pem \
  https://kea-server:8443
```

## Stale-Label Timeout

By default, metrics for an unreachable server are kept indefinitely. To
automatically remove them after the server has been silent for a given period:

```bash
# Remove stale metrics after 5 minutes of no response
kea-exporter --stale-timeout 300 http://kea-server:8000

# Or via environment variable
export STALE_TIMEOUT=300
kea-exporter http://kea-server:8000
```

A value of `0` (the default) disables the timeout — stale metrics are never
pruned automatically.

```bash
# Combine with multiple targets and other options
kea-exporter \
  --stale-timeout 600 \
  --timeout 15 \
  http://dhcp-server1:8000 \
  http://dhcp-server2:8000
```



Example Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: 'kea'
    static_configs:
      - targets: ['kea-exporter:9547']
    relabel_configs:
      # Optional: Use server label as instance
      - source_labels: [server]
        target_label: instance
```
