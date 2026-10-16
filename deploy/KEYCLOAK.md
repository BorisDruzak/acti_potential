# ACTI identity provider

Keycloak is the identity provider (IdP). It authenticates people; ACTI retains its
own user UUIDs, roles, registry/document ACLs and audit history. A domain login
does not grant access until an administrator explicitly links its exact
`(issuer, subject)` to an existing ACTI user. Names and email addresses never
merge accounts. Local emergency administration remains available.

## Deployment contract

- The optional Compose profile `idp` runs an optimized, digest-pinned Keycloak
  26.8.1 image. Nginx terminates HTTPS under `/idp/`; no Keycloak host port is
  published. The API's identity network permits the HTTPS back-channel; the
  worker, Redis and PostgreSQL remain on the internal network.
- PostgreSQL database `acti` has a separate `keycloak` schema owned by the
  non-superuser `acti_keycloak`. That role must have no privileges on ACTI
  tables. ACTI's application and migration roles must have no privileges on
  the Keycloak schema. Provision the schema before starting Keycloak.
- The existing full-database encrypted external backup includes both schemas.
  Restore verification must compare both schemas' tables, sequences and DDL.
  Provider signing keys and credentials are sensitive backup contents.
- Use production `start --optimized`, a 1536 MiB memory limit and at most eight
  database connections. Keycloak state persists in PostgreSQL; its container
  data directory is ephemeral.
- Keep all deployment values in the root-only runtime environment outside Git.
  Never commit realm exports containing secrets or private signing keys.

Set `COMPOSE_PROFILES=idp`, `ACTI_IDP_DB_PASSWORD`, and the temporary bootstrap
administrator variables `KC_BOOTSTRAP_ADMIN_USERNAME` /
`KC_BOOTSTRAP_ADMIN_PASSWORD`. After creating a permanent protected provider
administrator, remove bootstrap credentials from the runtime environment.

Create realm `acti` with self-registration and email-based login disabled,
brute-force protection enabled, and a confidential client `acti-web`. Enable
authorization code flow and PKCE S256 only. Disable implicit flow, password
grant and service accounts. Permit exactly the application's HTTPS origin plus
`/api/v1/auth/oidc/callback` as the redirect URI, without wildcards.

Then set `ACTI_OIDC_ENABLED=true`, `ACTI_OIDC_ISSUER` to the realm's public HTTPS
issuer, `ACTI_OIDC_CLIENT_ID=acti-web`, and `ACTI_OIDC_CLIENT_SECRET`. The API
trusts the mounted ACTI CA and verifies issuer, audience, signature, expiry,
nonce, state, PKCE and browser binding. Failed or unlinked identities are denied.

## Active Directory boundary

Use LDAP federation in `READ_ONLY` mode, a dedicated ordinary directory reader,
and verified LDAPS or StartTLS. Do not store a domain administrator password,
disable certificate verification, write user attributes/passwords to AD, or
automatically translate AD groups into ACTI roles or confidential-document ACLs.

Check the directory certificate's DNS SAN, server-auth EKU, chain and expiry
from the provider host. AD TLS provisioning is a separate change on a domain
controller; the ACTI deployment does not install certificates or restart AD.
Use `objectGUID` as the stable LDAP identity. Disable LDAP referrals and broad
scheduled synchronization; admit users through the explicitly approved scope.

Local provider pilot accounts verify OIDC only. They do not prove real domain
authentication. Task 011 remains blocked until an actual domain account and the
required roles, denials, local fallback, restart and recovery checks pass.

## Verification

Verify real HTTPS browser redirects and login, retained ACTI UUID and roles,
reader/editor/admin denials, unknown-identity rejection, unlink/session
revocation and the unchanged emergency local login. Stop only Keycloak and
confirm the local login remains usable; restore it and repeat SSO. Run the
external backup and isolated restore with both schemas present. Never restore
provider sessions or signing keys into a public test issuer.

Reference: [Keycloak production configuration](https://www.keycloak.org/server/configuration-production),
[reverse proxy](https://www.keycloak.org/server/reverseproxy),
[LDAP federation](https://www.keycloak.org/docs/latest/server_admin/#_ldap).
