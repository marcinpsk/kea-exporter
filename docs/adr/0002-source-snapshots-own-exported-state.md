# Source snapshots own exported metric state

The exporter stores the last successfully interpreted metric samples for each Source as immutable state. Prometheus exposition reads these Source snapshots through its collector interface. A successful Source replaces its snapshot. A failed Source keeps its previous snapshot until recovery or the stale timeout removes it. This design replaces mutable Gauge children as durable state because Gauge mutation requires separate reversal bookkeeping and couples exposition to scrape-cycle updates.

## Considered options

**Mutable Gauge children.** Rejected because stale labels require `LabelLifecycle` to reverse earlier writes. Correctness then depends on ordering across publication, lifecycle recording, and scrape-cycle completion.

**Raw Kea responses.** Rejected because exposition would repeat Catalogue interpretation and retain transport-specific data after the scrape cycle completes.

## Consequences

The Catalogue remains the declarative schema and continues to apply the exact Statistic and Scope rule from ADR-0001. The exporter publishes a Target result only after every successful Source in that result validates. A Scrape cycle builds its replacement state privately and publishes it with one swap after all Targets finish. The collector captures that completed state without holding the Scrape-cycle lock while it materializes exposition.
