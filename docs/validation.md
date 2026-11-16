# Validation scope

This repository contains an explicitly selected/generalized source extraction,
not the owner's machine state. Run counts and CI results must be recorded after
execution for this export. Tests never establish live-host compatibility by
themselves.

Python tests exercise telemetry/staleness, HTTP authentication and Origin/Host
rejection, process confirmations, session selection, cron/unknown-container
refusal and maintenance recovery. Chart tests cover timestamps and gaps. System
operations are mocked; no test powers off, wakes, restores over or changes a real
server. Demo acceptance uses only synthetic values and local HTTP.

The optional SSO lab tests real HTTP/TLS, password/MFA, PKCE/state, native OAuth,
role denial and central HTTP revocation. Shortened-session tests are separate
from observing a real 15-minute production session. Lab certificates, state and
credentials are not release artifacts.

Not established: production SSO rollout, actual suspend/power states or wattage,
WOL on arbitrary hardware, WAN relay, real-device notifications/calls, data-loss
objectives, or complete restore of a new physical server. No production installer
or personal one-off repair is advertised as portable.

Public-export checks on 2026-11-16: 39 Python tests, chart timestamp/gap tests,
local Markdown links and JavaScript syntax passed. The synthetic Edge smoke test
passed on desktop, light theme and 390-pixel mobile width, including action
rejection. The local Docker engine was stopped; the public SSO integration is
therefore assigned to the disposable Linux workflow rather than being claimed
as a fresh local run.

Published source revision `9b7c7fc` passed the
[Windows/Linux monitoring workflow](https://github.com/Fogbose/HomeServer-Monitoring/actions/runs/43889757970)
and the [disposable Linux SSO workflow](https://github.com/Fogbose/HomeServer-Monitoring/actions/runs/43889842954)
on 2026-11-16. The latter completed **67 checks**, including native Beszel/OliveTin
OAuth and MFA, plus the real shortened-session inactivity-expiry test; its normal
session policy was restored and lab containers stopped. These results apply to
synthetic CI fixtures and do not change the production limitations above.
