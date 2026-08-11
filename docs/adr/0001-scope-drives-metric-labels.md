# Statistic scope drives metric labels

Every catalogue entry declares the scope of its statistic, and the metric's label set is derived from that scope rather than written by hand. Global scope gives `server`, subnet scope adds `subnet` and `subnet_id`, pool and pd-pool scope add `pool` and `pd_pool`. Labels that are not a function of scope, such as `operation` and `context`, stay on the entry as extra labels.

## Considered options

**Scope as advisory.** Keep the hand-written label sets and only cross-check them against scope when the exporter starts. Rejected: the label set stays a second place to get wrong, which is the failure this change exists to remove.

**Scope as a set per entry.** Let an entry list every scope Kea reports the statistic at. Rejected as redundant: declaring the deepest scope is enough, because a reading shallower than the declared scope is either a global aggregate we suppress or a subnet total we export with an empty pool label.

## Consequences

A reading deeper than the declared scope is skipped and reported once, instead of being written to a metric that has no label to distinguish it. Before this decision, two pools reporting the same subnet-scoped statistic silently overwrote each other, last write winning, with nothing logged. Kea has not reported such a statistic since pool scope arrived in 2.4.0, so the fault was latent rather than live.

Global readings of a statistic declared at subnet scope or deeper are suppressed without a report, because Kea emits them routinely as aggregates. This replaces a hand-maintained list of 22 statistic names per daemon, which had to be extended by hand whenever Kea added a global aggregate. Kea 3.2 adding global `assigned-addresses`, `assigned-nas`, and `assigned-pds` is the case that motivated it.

Statistics the exporter deliberately does not expose are named in one set per daemon rather than split across a global list and a subnet list, since every entry of the subnet list already appeared in the global one.
