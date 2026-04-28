

0.9.0



 - drop pool label from na_reuses_total and pd_reuses_total

 - drop pool label from na_registered_total; clarify pkt6-addr-reg-reply-received





 - update README/EXAMPLES for new options, add CI badge, fix .coverage gitignore





 - add support for DHCPv6 address registration metrics (Kea 2.5.5+)







0.8.0



 - semver build fix

 - materialise target.stats() before mutating gauges

 - retry limit, narrow gauge.remove exception, SIGINT before shutdown

 - use recv() loop in UDS query() to respect socket timeout

 - track (server_id, dhcp_version) pairs in stale-label pruning

 - address CodeRabbit review findings on stale-label timeout





 - add --no-tls-verify and --ca-bundle options for TLS verification

 - implement configurable stale-label timeout







0.7.9





0.7.8



 - use inputs.tag to detect release call in Docker workflow

 - update release.yml

 - guard subnet id access in uds.py and restore TestURLParsing class

 - address code review findings

 - stricter Kea RPC validation and bounds check in stats()

 - address PR comments

 - udpate ruff pin to match pre-commit hooks / moved sys import

 - address PR comments

 - update readme / address PR comments

 - address PR comments + add shared_network subnet extraction

 - sanitize credentials in error output and clean up project artifacts

 - add error resilience, HTTP status checks, UDS socket timeout, and workflow permissions









0.7.7



 - docker build fix to include event PR #2







0.7.6



 - docker build fix to include event PR









0.7.5



 - docker build







0.7.4



 - auth for cleanup workflow fix

 - auth for cleanup workflow fix

 - update semantic-release pyproject.toml

 - update cleanup workflow

 - tighten tag generation for docker images

 - add cleanup of old PR container packages







0.7.3



 - move relase workflow to staging branch, as main is protected - we PR staging to main if everything checks out ok







0.7.2



 - update workflows/release with pinning, updated to lts/*

 - update workflows/release

 - correct parsing subnets/add release tracking

 - add suggested change

 - run container as non-root

 - add kea-exporter home to $PATH

 - build also on linux/arm64

 - arm build

 - set up docker buildx

 - bad copy/paste registry

 - set up docker buildx input

 - add documentation

 - anchor link

 - anchor link typo

 - remove anchor section

 - add missing requests dependency





 - convert to pep517 style build-system





 - add docker build

 - add docker build

 - add dockerfile labels

 - add authors label

 - add default values to env vars

 - add environment variable

 - copy only required files

 - add mTLS
