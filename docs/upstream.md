# Upstream components and licenses

Repository-owned console/helpers/docs are MIT. Third-party application source,
executables and container layers are not redistributed in this Git tree. Their
own licenses and update lifecycles apply. Image references are explicit versions
and digests, not a claim that each pin is the latest security release.

- [Authelia](https://github.com/authelia/authelia): authentication provider.
- [Beszel](https://github.com/henrygd/beszel): metrics hub/agent.
- [OliveTin](https://github.com/OliveTin/OliveTin): predefined actions and reports.
- [Nginx](https://nginx.org/): private gateway.
- [Cockpit](https://cockpit-project.org/): separately installed system administration.
- [Tailscale](https://tailscale.com/docs/): optional private network; no account/key is supplied.
- [Nextcloud](https://nextcloud.com/): optional observed workload; no core source is bundled.

Review upstream documentation/advisories before installing or updating. Never
force app compatibility, disable TLS or mount an administration socket simply
to make an integration test pass.
