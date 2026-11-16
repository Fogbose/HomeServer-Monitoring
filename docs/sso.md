# SSO reference and isolated lab

The reference combines Authelia MFA with native Beszel and OliveTin OAuth2.
OliveTin occupies the origin root (upstream no longer supports subpaths),
Authelia uses `/auth`, Beszel `/metrics`, and the authenticated portal `/home/`.
The browser uses verified HTTPS. Token/userinfo exchanges use a separate internal
TLS listener and preserve the canonical public issuer. They never disable
certificate checks or pretend a plain HTTP token request was TLS.

The renderer takes a site-owned HTTPS DNS origin, generated client/session/storage
secrets, a password digest and a signing key. It returns configurations but
does not create real accounts, certificates, host policy or production services.
No personal production installer, deployment hash or migration archive is included.

**Production SSO is not validated by this public extraction.** A reference
deployment stopped during preparation before application cutover; its precise
private diagnostic is intentionally not published. Re-entering a password does
not justify overwriting an unknown partial installation. Inspect and repair only
known owned files.

The IdP and system accounts remain separate. Matching password values do not
make them synchronized identities. Cockpit retains PAM/sudo authentication. A
central logout blocks new protected HTTP requests with retained app cookies or
tokens, but does not kill existing SSH sessions, commands or Cockpit WebSockets.

## Run synthetic tests locally

Prerequisites: Python 3.10+, Docker Compose and OpenSSL. On Windows, the Git for
Windows OpenSSL path is a fallback; alternatively set `HOMESERVER_OPENSSL` for
this process. No global PATH or Windows trust-store changes are performed.

```sh
python infrastructure/monitoring/sso-lab.py prepare
python infrastructure/monitoring/sso-lab-server-profile.py
```

The second command prepares/runs the server profile and stops it after testing.
On Linux, prepare fixtures before explicitly setting container data ownership:

```sh
python infrastructure/monitoring/sso-lab-server-profile.py --prepare-only
sudo python3 tools/lab_permissions.py
python infrastructure/monitoring/sso-lab-server-profile.py --run-prepared --expiry
```

Only the fixed `private/monitoring/sso-lab` fixture is eligible for this ownership
helper. Never point it at a deployment. The initial three-origin profile is a
separate fixture layout; do not run competing profiles simultaneously.

Synthetic credentials, signing keys and lab CA stay ignored under `private/`.
The CA is trusted only by the test clients/containers, never installed system-wide.
The laboratory network isolates applications and exposes only loopback 19446.
No server SSH key, Docker socket, real accounts or administration actions are
provided. External NTP checks cannot reach the Internet in this isolated network;
the lab does not disable them to hide the warning. Production clock synchronization
and a publicly trusted private-site certificate remain deployment prerequisites.

See [validation](validation.md) for limits and [upstream notices](upstream.md).
