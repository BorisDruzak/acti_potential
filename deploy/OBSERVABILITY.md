# ACTI read-only Server Observer contract

ACTI is observed by the existing Server Observer in `web_ovpn`. It does not
receive an ACTI application account, a PostgreSQL password, or the operator's
SSH identity. The existing observer timer and redacted Server Health view are
the intended collection and presentation paths.

## ACTI target

Install the reviewed `observe.py` as the regular, root-owned executable
`/usr/local/sbin/acti-v3-observe` (0755). Its isolated Python interpreter,
absolute executable paths, fixed commands and lack of command-line options
are part of the privilege boundary. It must not import code from a release
directory writable by the application account.

Provision `/etc/acti-v3/observer.json` as a regular root-owned file (0600),
with only `format_version` (1), `expected_postgres_uuid` and
`expected_documents_uuid`. Fill the UUIDs from the verified storage contract;
never guess them or commit the populated configuration.

Use a dedicated ordinary VM account with no Docker-group membership. Its
root-owned authorized-key file must accept only the existing observer public
key, limited to the verified monitoring source address, with `restrict` and
a forced command:

```text
/usr/bin/sudo -n /usr/local/sbin/acti-v3-observe
```

Grant sudo only for that executable **with no arguments**. Validate the
sudoers entry with `visudo` before installing it. The account must not be
able to modify its key file, helper, configuration or sudoers entry. Do not
copy an operator key or the monitoring private key to ACTI. Keep SSH host-key
verification enabled; pin ACTI's host key through the already trusted channel.

The helper returns only this versioned, Boolean aggregate:

```json
{
  "format": "acti-observer-v1",
  "services": {
    "frontend": false,
    "api": false,
    "postgres": false,
    "redis": false,
    "worker": false,
    "keycloak": false
  },
  "worker_heartbeat": false,
  "mounts_verified": false,
  "storage_capacity": false,
  "outbox_healthy": false,
  "backup_fresh": false,
  "backup_check_fresh": false
}
```

False means the check is not proven healthy. It must not expose container
IDs, filesystem UUIDs, paths, queue contents, credentials, identities or raw
exception text. Collection performs no writes, retries of business work,
service starts, mount changes or remediation. PostgreSQL access is only to
the ACTI target, in a bounded read-only transaction; SQL Server is not contacted.

The monitoring host also checks ACTI HTTPS live/readiness and OIDC discovery
independently of the SSH result. Provision only the public ACTI CA certificate
at the observer's fixed CA path; keep hostname and chain validation enabled.
URLs, SSH destinations, source addresses and populated profiles stay local.

## External-backup target

The `acti_backup` observer role reads the existing `ACTIBackup` Windows
Application event. It checks watchdog freshness, snapshot freshness and
destination capacity. It does not modify files, invoke backup/prune, create
another Windows task, or change the existing file-server role.

ACTI's own `backup_fresh` and `backup_check_fresh` checks inspect successful
protected backup receipts and native service/timer state. Recent metadata is
not proof of a new successful restore; retain the existing full repository
check and isolated restore procedure in [EXTERNAL_BACKUP.md](EXTERNAL_BACKUP.md).

## Acceptance boundaries

Before enabling a populated target, check actual SSH command confinement,
strict HTTPS from the monitoring host, the live redacted snapshot and its
authenticated browser view. Verify a critical condition and subsequent
recovery, preserving all pre-existing targets. Record deployment artifact
hashes separately from the ACTI application revision.

An in-panel critical state is not email, Telegram or another external
notification delivery. Do not claim such delivery without a configured and
tested channel. SQL TLS and VM reboot remain separate readiness gates.
