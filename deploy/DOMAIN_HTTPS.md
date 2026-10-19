# Canonical domain HTTPS

The original `server.crt`, `server.key` and `ca.crt` remain the management TLS
identity, including the localhost health check. A public domain can use a
separate CA-issued leaf without replacing that identity or its CA private key.
All populated configuration and key material stays outside Git.

## Prepare

1. Confirm the domain's A/AAAA records point only to the intended ACTI host.
2. Verify the domain certificate's chain, hostname, server-auth purpose,
   validity and matching private key. Transfer the leaf/key over authenticated
   SSH directly to the ACTI host; do not transfer the issuing CA private key.
3. Store `domain.crt`, `domain.key`, `domain-ca.crt` in `/etc/acti-v3/tls`,
   owned by root. The directory is mode 0700, private key 0600, public files
   0644. Verify the issuing public CA is trusted by client machines.
4. Create root-owned `domain-public.conf` there, mode 0644. It contains an
   additional `server` block copied from the committed HTTPS server in
   `deploy/nginx.conf`, with the exact domain as `server_name` and the domain
   certificate/key paths. Preserve the API/IdP proxies, limits and security
   headers. Omit the `legacy-ui-*.conf` include from this domain server.
   The main nginx config includes `domain-*.conf` through its read-only TLS
   directory mount. Missing optional files keep the original deployment valid.
5. Back up the runtime environment, TLS/config files, IdP configuration and
   database. Verify the backup and required data mount UUIDs before activation.

## Activate

Set the private runtime values:

```dotenv
ACTI_ORIGIN=https://acti.example.invalid
ACTI_PUBLIC_HOST=acti.example.invalid
ACTI_HTTPS_CA_FILE=/etc/acti-v3/tls/domain-ca.crt
ACTI_TLS_RENEWAL_ORIGIN=https://localhost
ACTI_OIDC_ISSUER=https://acti.example.invalid/idp/realms/acti
```

The frontend alias on the internal Docker network makes the canonical name
resolvable for the API without hardcoded container IPs. Recreate only the
services whose configuration changes, using `--no-deps` after mount preflight;
never run migrations, `compose down` or recreate PostgreSQL/Redis for this change.

For an existing Keycloak realm, update its managed ACTI client's exact callback
URI and public hostname together. Confirm the realm ID, signing keys and subject
IDs are unchanged. Move only the existing ACTI external-identity issuer values
from the exact previous issuer to the exact new issuer in one checked
transaction; preserve identity IDs, user IDs, subjects, active flags and sessions.
Reject conflicting destination mappings. Update the private provisioning and
federation ownership receipts' origin values without changing credentials or AD.
Do not create new identity links, users, roles or AD writes during a hostname move.

Once the domain works, `legacy-ui-public.conf` can contain
`return 308 https://acti.example.invalid$request_uri;` to redirect the original
UI location. API and management health routes remain available. Do not redirect
the domain virtual host back to itself.

## Verify and recover

Run `nginx -t` before reload; verify the selected leaf with SNI and strict TLS.
Check HTTP redirects, HTTPS readiness, security headers, local login, full OIDC
login/callback and a read-only original download. Confirm Office launch links
use the canonical domain. Check the original localhost/IP health check too.
Compare document/version/registration fingerprints and unchanged data-container
IDs against the pre-change snapshot.

Keep an exact root-only rollback receipt with the previous environment,
certificates, managed client configuration, issuer mappings and container
configuration. On failure restore those inputs together and verify the previous
strict HTTPS endpoint. Domain certificate renewal belongs to its issuing CA;
`renew-tls.py` continues to manage only the original local certificate.

## Keep domain security headers current

The root-owned domain server is a separate nginx virtual host. Updating the
container's default server does not update its copied headers. When changing
security headers in deploy/nginx.conf, apply the identical reviewed change to
every active domain virtual host before verifying the canonical origin.

ACTI Sync needs connect-src to allow only the sixteen fixed HTTP loopback
origins at 127.0.0.1 ports 43127 through 43142, in addition to self. Preserve
all other CSP directives. Verify the actual canonical /work response rather
than only the image's default configuration: an older restrictive CSP also
blocks automatic discovery. Run nginx -t, reload with the exact previous
configuration available for rollback, then exercise browser discovery over
strict HTTPS. Never use a wildcard or disable CSP/TLS to make it connect.
