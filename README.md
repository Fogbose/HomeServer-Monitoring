# HomeServer Monitoring

A separate MIT module for a private home-server monitoring console, operational
architecture, guarded maintenance references and an isolated SSO laboratory.
The dashboard UI is French; the public setup documentation is English.

**Status: experimental public extraction.** The local dashboard/demo and tests
are usable. Privileged Linux helpers and the SSO renderer require deployment
review; there is **no unattended production installer**. The originating SSO
deployment stopped before cutover. Its personal installers are not included.

## Try the console without a server

Python 3.10+ is sufficient. No API account, Docker, SSH identity or administrator
rights are needed for this demo:

```sh
git clone https://github.com/Fogbose/HomeServer-Monitoring.git
cd HomeServer-Monitoring
python infrastructure/monitoring/console.py --demo
```

The browser opens a token-protected console on **127.0.0.1:18765**. Only synthetic
data is shown and **every action is refused**. Close the console with its button
or Ctrl+C. Runtime data and the launch URL stay under ignored `private/`.

Included: timestamped CPU/RAM/storage charts, service states, backup freshness,
events, stale-data guards, theme switching and mobile-width layout. Missing SSH
or telemetry means **unknown**, not proof the server is asleep or off.

## Connect your own deployment

```sh
python tools/configure.py
# Edit private/monitoring/site.json and supply your own verified SSH files.
python infrastructure/monitoring/console.py
```

Follow [configuration](docs/configuration.md) and [architecture](docs/architecture.md).
There is no automatic host-key enrollment, password collection, service
installation, network change or wake. Hostname matching is explicit. Actions
are disabled until deliberately enabled and their target helpers reviewed.

The console can read an existing privileged cache or feed the read-only Linux
probe through SSH. Optional Cockpit forwarding uses loopback only. Beszel and
OliveTin are separately operated tools; their login is not inferred from a link.

## Maintenance and SSO

- [Operations and recovery](docs/operations.md): persistent preparation state,
  guardrails and recovery after an abandoned shutdown preparation.
- [SSO laboratory](docs/sso.md): Authelia, native Beszel/OliveTin OAuth, TLS,
  MFA, denied roles and central HTTP-session revocation using fictitious data.
- [Validation and limits](docs/validation.md): what the public export verifies.

Cockpit keeps its own system authentication and privilege elevation. SSO HTTP
logout does not terminate an already-open Cockpit WebSocket, shell or running
command. Windows/Linux sleep behavior, WOL, watts and hardware RAID require
independent measurement. RAID does not replace versioned/off-machine backups.

## Development

```sh
python -m unittest discover -s tests -v
python tests/check_links.py
node infrastructure/monitoring/test-chart.cjs
node --check infrastructure/monitoring/dashboard.js
node --check infrastructure/monitoring/sso-home.js
```

Optional browser smoke test: `npm ci`, `npx playwright install chromium`, then
`npm run test:ui`. It starts and closes its own synthetic demo on loopback 18776,
tests desktop/light/mobile rendering and confirms action rejection. Screenshots
stay ignored in `.work/`; no personal browser profile is accessed.

Tests use synthetic records and mocked system operations. They never call a
personal server or issue power/WOL commands. The SSO integration is a separate,
manual Docker workflow; it does not deploy to any real host.

## Related projects

| Repository | Responsibility |
| --- | --- |
| [HomeServer](https://github.com/Fogbose/HomeServer) | Nextcloud host/Compose, storage, ingress, backup and Talk/media infrastructure |
| [HomeServer-Software](https://github.com/Fogbose/HomeServer-Software) | Maison, App Store catalogue and client guides |
| This repository | Monitoring console, telemetry/maintenance references, SSO lab and monitoring architecture |

See [contributing](CONTRIBUTING.md), [security](SECURITY.md) and [license](LICENSE).
