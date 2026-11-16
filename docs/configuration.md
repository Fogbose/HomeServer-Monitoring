# Configuration

The demo has no prerequisites beyond Python. For live collection, initialize
with `python tools/configure.py`, then edit ignored `private/monitoring/site.json`.
The `.invalid` hostname is intentionally refused. `hostname` must exactly match
the probe's OS hostname; `ssh.host` is your verified network endpoint.

Provide your own existing SSH identity and independently verified known_hosts
file. Paths are relative to this checkout unless absolute. No key is discovered,
copied from another project, enrolled or uploaded automatically. SSH uses batch
mode, identities-only and strict host-key checking. Public examples contain no
working site configuration.

Leave `allow_actions` false until the Linux helpers, registry and confirmations
have been reviewed on your own disposable installation. The public export does
not provision backup services or bind personal backup scripts to the dashboard.
The LAN wake helper binds only the configured local interface and subnet. WAN
wake/ESP32 relays remain a design extension, not implemented behavior.

Set `enable_cockpit_tunnel` true only when Cockpit already listens on server
loopback 9090 and that forwarding is authorized. The tunnel binds local 19090;
Cockpit still asks for its own system credentials. Beszel/OliveTin binaries and
accounts are not auto-provisioned by the console.

Optional tool links use ignored `private/monitoring/tools-status.json`:

```json
{
  "beszel": {"ready": false, "status": "Not configured", "url": "http://127.0.0.1:18090"},
  "olivetin": {"ready": false, "status": "Not configured", "url": "http://127.0.0.1:18337"}
}
```

Only mark a tool ready after verifying it. A ready link is not proof of an
authenticated browser session or root privileges.

For a root cache, `probe.py --cache /var/lib/homeserver-monitoring/status.json
--cache-group YOUR_EXISTING_READER_GROUP` must be run by a separately reviewed
system unit. Do not broaden a private directory's permissions to make a failing
read work. The probe assumes the documented `/srv` storage/capture conventions
and loopback Nextcloud status endpoint; adapt them explicitly for a different host.
