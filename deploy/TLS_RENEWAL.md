# Manual ACTI HTTPS certificate renewal

`renew-tls.py` renews only ACTI's server certificate. The current CA, CA key,
server key, SANs, subject and certificate purposes remain unchanged. It does not
modify AD/LDAPS, SQL Server, users, data mounts or application images, and does
not reboot or restart the stack. There is no automatic renewal timer.

The production CLI accepts only the expected committed source SHA and its own
operation UUID. Runtime, TLS and receipt paths are fixed. Run the helper from
the active root-owned committed release as root. It checks the runtime SHA,
exact frontend container/image/SHA and protected filesystem paths.
It takes the **same exclusive lock** as external backup:
`/run/lock/acti-v3-external-backup.lock`. Backup and renewal cannot overlap.

## Prepare without activating

```sh
sudo python3 /path/to/active-release/deploy/renew-tls.py --expected-sha FULL_SHA prepare
```

The default threshold is 30 days. A healthy certificate outside that threshold
returns `not_due`. For an explicitly planned renewal exercise, append `--force`.
Preparation checks the currently served certificate and strict HTTPS readiness.
It verifies the existing CSR's signature, exact subject and public key before
issuing a new 365-day leaf into its own root-only operation directory.

The `openssl x509` command uses an explicit random `-set_serial`, `-extfile`
and `-extensions`; the CA serial file is not modified. The candidate must match
the existing subject, complete SAN set, EKU, key usage, basic constraints and
server public key. Its serial must change, its validity must exceed 30 days,
its lifetime must not be shortened, and it must not outlive the CA. The chain,
server purpose, all SAN names and the actual deployment host are verified.

The helper retains the previous public certificate, candidate and a protected
receipt under `/var/lib/acti-v3/tls-renewal/<operation-uuid>/`. Private keys stay
in `/etc/acti-v3/tls`; they are never exported. Key checks use the existing
unattended local key access. Receipts and certificate profiles contain private
deployment metadata and must remain outside Git and public logs.

## Apply and roll back

```sh
sudo python3 /path/to/active-release/deploy/renew-tls.py --expected-sha FULL_SHA apply --run-id OPERATION_UUID
sudo python3 /path/to/active-release/deploy/renew-tls.py --expected-sha FULL_SHA rollback --run-id OPERATION_UUID
```

Apply rechecks every pinned input hash, owned receipt/artifact and the frontend
identity. It atomically replaces **only `server.crt`**, retaining the matching
server key and the inode of the bind-mounted TLS directory. It runs `nginx -t`
and reloads only the exact pinned frontend container. New strict TLS handshakes
must present the expected leaf fingerprint; the API must return genuine positive
JSON readiness checks. Redirects, ambient proxies and disabled certificate
verification are rejected.

If activation or verification fails, the helper atomically restores the previous
certificate and validates its activation. Failed rollback produces a failure,
never a success receipt. Manual rollback requires the same known deployment,
owned operation and expected active candidate; a changed deployment or certificate
must be investigated before further action. Interrupted or failed operation state
is preserved for recovery rather than silently adopted or deleted.

The TLS directory remains root-owned `0700`, both keys remain `0600`, and the
active public certificate is `0644`. The helper does not introduce symlinks:
the existing encrypted recovery copier rejects them. Old receipts are not
automatically removed, and their saved previous certificate must be retained
for the agreed rollback window.

## Acceptance and maintenance

For a renewal exercise, prepare, apply, verify strict HTTPS, roll back, verify
the old fingerprint, then apply and verify the new fingerprint again. Check
local emergency login, corporate login and application readiness afterwards.
Record the SHA, operation receipt and before/after validity privately.

This procedure proves repeatable manual renewal. Assign an operator and a due
date before expiry; unattended scheduling and expiry alert delivery require
their own configured operational mechanism. Independent console access and
post-reboot acceptance remain separate Task 011 conditions.
