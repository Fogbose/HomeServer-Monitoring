# Security

Experimental code, without a security SLA or production-readiness claim. Do not
post credentials, QR codes, server identities, databases or exploit details in
public issues. Use GitHub private vulnerability reporting when available;
otherwise request a private contact without publishing sensitive details.

The local console requires an unguessable bearer token and exact Host/Origin
checks, but it does not defend against malware already running as its owner.
Protect private files using OS account permissions. Never use NOPASSWD, disable
host-key checks, skip TLS verification or mount a Docker socket into the web tools.
