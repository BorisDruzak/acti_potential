# External ACTI backup

The external backup contains a consistent custom PostgreSQL dump, every
registered original version, table and sequence digests, a semantic schema
fingerprint, and protected recovery copies of deployment configuration and the
committed release. It never reads ACTI v2 SQL Server or migrates source data.

`restore-verify.py export --expected-sha SHA` creates a private, owned bundle on
the verified document mount. The source manifest and schema must remain stable
through dump and file copying; otherwise export fails. Originals are verified
by size and SHA-256. The existing PostgreSQL-only `backup.sh` remains available.

`external-backup.py` uploads bundles to one dedicated, identity-pinned encrypted
Restic repository. Only complete, confirmed snapshots permit retention. The
fixed policy keeps the last three snapshots, 14 daily, eight weekly and 12
monthly recovery points, grouped by host and tag. These are overlapping retention
classes, not a promise of 37 separate full copies. No unrelated repository,
snapshot, source file, or working database can be selected for pruning.

## Runtime prerequisites

- Root-owned protected `/etc/acti-v3/runtime.env` and committed release.
- Correct PostgreSQL/document mount UUIDs; preflight runs before any data write.
- Distribution-packaged Restic 0.16.4 and Rclone 1.60.1, including vendor security
  patches; record exact package versions in private deployment evidence.
- New dedicated destination directory, local non-admin account and group.
  `provision-backup-sftp.ps1` refuses existing names/directories, scopes SSH to
  key-only internal SFTP with chroot and no forwarding, and preserves existing
  administrator settings. Apply has automatic rollback unless fresh access is
  confirmed. Inspect the original SSH access policy before executing it.
- Private `/etc/acti-v3/backup`: `config.json`, `ssh_config`, `rclone.conf`,
  `known_hosts`, `id_ed25519`, and `repository-password`; files mode 0600,
  directories 0700. Escrow the password and recovery access outside both servers.
  Never put these files or machine-specific values in Git.

Restic uses the Rclone SFTP bridge because Windows OpenSSH chroot has an upstream
`fchmod` compatibility defect. Rclone runs on the ACTI VM over standard input;
no extra listener, Windows transfer daemon, weakened chroot, or TLS bypass is
required. Its known-hosts file is mandatory and pinned through the existing
verified administrative SSH channel.

Runtime configuration keys are `format_version`, `expected_repository_id`,
`restic_repository`, `ssh_config`, `password_file`, `cache_dir`, and `state_dir`.
The runner validates the fixed supported backend and protected paths. The
Rclone remote must match the validated SSH host, port, user, key and known-hosts
file; arbitrary commands or ambient remote settings are rejected.

## Schedule and failure monitoring

Install the committed runner as `/usr/local/sbin/acti-v3-external-backup` and the
four supplied systemd units. Daily backup runs at 03:15 Asia/Yekaterinburg, with
up to five minutes jitter. Full encrypted repository reading/check runs Sundays
at 06:15 in that timezone. Both require the data mounts and serialize through a
shared lock. Failures produce nonzero service status, journal entries, private
diagnostics, and per-run receipts; incomplete snapshots are never success.

On the destination, schedule `windows-backup-watchdog.ps1` as SYSTEM with its
owned root and owner UUID. It independently reports stale/missing snapshots,
low free space, excessive repository size, unsafe paths and bounded-scan errors
to the `ACTIBackup` Application event source, plus private JSON receipts. It
does not delete files or modify other shares. The thresholds are alerts, not
filesystem quotas. Email/webhook delivery requires a separately configured
existing channel; native event monitoring does not prove external delivery.

## Restore acceptance

1. `external-backup.py backup` returns the full confirmed snapshot ID.
2. `external-backup.py check` reads and verifies the actual external repository.
3. `external-backup.py fetch --snapshot ID` downloads/decrypts that exact snapshot
   to a new private directory and uses Restic restore verification.
4. `restore-verify.py prepare --expected-sha SHA --bundle-source DOWNLOADED_BUNDLE`
   consumes only the retrieved dump and originals. Missing/corrupt files fail;
   there is no fallback to live storage. The bundle SHA must match the running
   release; recover its archived release first when testing an older snapshot.
5. All table/sequence digests and schema fingerprints must match before copied
   sessions and service tokens are revoked in the owned clone. Run real HTTPS
   browser smoke with fresh login and original downloads. For a copied-cookie
   canary, issue the source cookie **before** making the external snapshot.
6. Use receipt/run-ID cleanup for the owned clone/export. Preserve the external
   recovery point. Never restore over production for an acceptance check.

This proves an external backup can restore into an isolated environment on the
existing VM. It does not prove bare-metal recovery or physical storage failure
independence. The ordinary repository credential permits retention, so this is
not an immutable or ransomware-proof repository.

References: [Restic SFTP issue](https://github.com/restic/restic/issues/4335),
[Windows OpenSSH defect](https://github.com/PowerShell/Win32-OpenSSH/issues/2263),
[Restic backends](https://restic.readthedocs.io/en/v0.16.4/030_preparing_a_new_repo.html),
[Rclone SFTP](https://rclone.org/sftp/).
