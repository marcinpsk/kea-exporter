# kea-exporter

Reads statistics from ISC Kea DHCP servers and exposes them as Prometheus metrics.

## Language

### Kea side

**Target**:
One Kea control API the exporter reads from, addressed either by URL or by Unix socket path.
_Avoid_: host, instance, endpoint, node

**Daemon**:
DHCP4, DHCP6, or DDNS. A target can expose more than one, and each reports its own statistics.
_Avoid_: module, service, DHCP version

**Statistic**:
A named value Kea reports, such as `assigned-addresses`. Always a Kea-side name.
_Avoid_: metric, stat, counter

**Scope**:
Where a statistic is reported: global, subnet, pool, pd-pool, or DDNS key. A statistic reported at subnet scope or deeper carries a subnet selector in its name.
_Avoid_: level, granularity, context

**Subnet**:
An address range Kea serves, identified by a numeric id that appears in the statistic name.

**Pool**:
A block of addresses inside a subnet. Distinct from a pd-pool.
_Avoid_: range

**pd-pool**:
A prefix range inside a subnet, used for prefix delegation. A separate scope from pool: the two never apply to the same statistic reading, and one statistic can be reported under both.
_Avoid_: prefix pool, delegation pool

**DDNS key**:
A TSIG key name that DDNS statistics are reported per, appearing as a selector in the statistic name.

### Exporter side

**Metric**:
A Prometheus gauge the exporter exposes. Always an exporter-side name.
_Avoid_: statistic, series

**Catalogue**:
The table that routes each statistic to its metric, one per daemon. The single place a statistic's metric, scope, and labels are declared.
_Avoid_: map, mapping, registry, lookup table

**Entry**:
One row of the catalogue, covering one statistic.
_Avoid_: mapping, rule, record

**Never-exported statistic**:
A statistic Kea reports that the exporter deliberately does not expose, because a finer-grained statistic already covers it.
_Avoid_: ignored metric, skipped metric

**Scrape cycle**:
One pass over every target, reading each one's statistics and updating the metrics.
_Avoid_: run, poll, refresh

**Source**:
One daemon on one target. A scrape succeeds or fails per source, and stale labels are tracked per source.
_Avoid_: instance, node, endpoint

**Stale label**:
A label combination present in the previous scrape cycle and absent from the current one, such as a renamed pool.
_Avoid_: orphaned label, dead series
