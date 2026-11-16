# Maintenance, recovery and energy

The root helper sources are experimental references, not an installer. Before
deploying, supply a root-owned `/etc/homeserver-power.json`, change the exact
hostname and Compose paths in the example policy, and create its root-owned lock
under `/run/lock`. All ancestors/configurations must pass the trusted-path checks.
Never copy a personal site's policy or enable actions before testing your own.

Preparation closes explicitly selected other SSH sessions, preserves the
controlling session, stops application admission and keeps persistent ownership
state under `/var/lib/homeserver-monitoring/`. A successful prepare means the
applications may intentionally be unavailable; it is not a completed power-off.
Resume or finish explicitly. Never equate an exit code of zero with all clients
being inactive without the dedicated prepared-state check.

Recovery clears maintenance only when the preparation is owned, the policy and
Compose hashes match, the application version matches and no migration is
pending. It starts existing containers rather than pulling/recreating them. The
wrapper timeout must exceed the Compose wait timeout. Foreign maintenance must
remain for investigation. A recovery timer must never power off automatically.

The reference supports abandoned-preparation recovery after 15 minutes, but its
system-unit integration must be supplied and tested separately. No automatic
shutdown, unattended upgrade or arbitrary root-command endpoint is published.
An absent TCP signaling connection is insufficient to prove TURN/UDP inactivity;
human confirmation of no calls remains mandatory.

| Item | Suggested operator review |
| --- | --- |
| Data/backup age, failed units, storage | Each use; alerting outside the open UI is not yet implemented |
| Coherent backup and off-machine encrypted copy | Regular automated schedule appropriate to your RPO |
| Restore into a new isolated destination | Before trusting a migration and after material changes |
| TLS certificates | Automated renewal plus observable failure/expiry alerts |
| Updates | Review compatibility, backup, apply in an idle window and verify |
| Power/suspend/WOL | Planned physical acceptance; never infer from mocked tests |

RAID/mirroring may improve availability but does not protect against deletion,
corruption, theft or loss of the enclosure. An attached USB SSD is not by itself
a RAID and is not evidence of an off-machine copy. Observe existing disks and
backups first; do not format, repartition or purge volumes as part of monitoring.

The DynamicUser example allocates an unprivileged systemd identity rather than
assuming an arbitrary numeric UID exists. On the target host, actual runtime
testing is still required; syntax validation alone does not prove service startup.
