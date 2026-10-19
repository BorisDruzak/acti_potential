# ACTI Sync v1 (Issue #21)

## Model and compatibility

Migration `0020_office_sync` adds logical originals, binary revisions, edit sessions
and idempotent save receipts. Every existing file gets its own logical original
with the same ID; it does not merge names, rewrite stored bytes or rewrite schema
values. A new ordinary upload still creates a separate attachment. The per-file
**Новая версия** action explicitly targets one logical original.

Current projections expose the stable `id`, immutable `current_file_id` and
`binary_revision`. Existing `/files/{id}/download` links remain immutable. Binary
saves change the pointer and content history, without incrementing the card's
metadata revision. The content index retires prior sources from current search
and queues extraction of the new immutable source in the same transaction.

ACTI does not yet have a legal designation distinguishing an editable working
binary from an official original. Until that contract exists, Sync and targeted
replacement allow only native, unregistered draft documents without imported
provenance or any previously published metadata revision. Merely switching a
published card back to draft does not authorize replacement. Existing manual
attachment upload and read-only Office/LibreOffice opening remain available under
their existing ACLs. Changing this working-copy policy requires an explicit
domain decision.

## API v1

Browser operations require the normal ACTI cookie, origin and CSRF checks:

- `POST /api/v1/office-sync/launch` with `{file_id}` issues a 60-second, single-use
  ticket, `session_id`, expiry and `acti-sync://launch?ticket=...` URI.
- `GET /api/v1/office-sync/files/{id}/status?mode=editor|viewer` reports server
  capability, current revision, editor, last contact and last committed save.
- `GET /api/v1/office-sync/files/{id}/history` exposes immutable binary history.
- `PUT /api/v1/office-sync/files/{id}/content` performs a targeted manual save.

The Windows client exchanges `{ticket, client_version}` at
`POST /api/v1/office-sync/exchange`. Only compatible major version 1 is accepted.
The response contains an edit-session bearer token, file identity, filename,
revision, SHA256, token expiry and lease expiry. Subsequent operations under
`/api/v1/office-sync/sessions/{session_id}` are `download`, `heartbeat`, `renew`,
`save` and `finish`. They require the scoped bearer, not a browser cookie or a
shared administrator credential. Renew rotates the token. Lost access requires
reauthorization through ACTI while retaining local changes.

Saves are raw DOCX with `If-Match: "<binary revision>"`, `Idempotency-Key`, and
`X-Content-SHA256`. Limits remain 25 MiB. Incomplete/encrypted/macro-containing
DOCX, mismatched hashes and reused keys with different bytes are rejected.
An identical replay returns its original committed receipt, including after a
later save. The 90-second lease admits one editing client per original. Every
operation rechecks the originating browser session, account, current document,
field membership and read/update/upload/download ACLs. Logout or loss of rights
revokes writing. Expired leases and stale revisions cannot silently overwrite.

Uploads spool bounded bytes before acquiring write locks. The locked transaction
rechecks authorization and CAS, creates an immutable object, changes the pointer,
records history/audit/outbox/receipt, and queues indexing. Rollback removes only
the newly uncommitted object. A save response is produced after commit.

## Windows application

`ACTISync.Service.exe` runs as **NT AUTHORITY\LocalService**. It never launches
Word or displays a window. `ACTISync.User.exe` runs in the interactive Windows
session, owns the tray and opens the managed local DOCX in installed Word.

The MSI pins `HKLM\SOFTWARE\ACTI\Sync\ServerOrigin` to an HTTPS origin. Neither
the protocol URI nor IPC can supply an arbitrary server or file path. Standard
certificate verification remains enabled, with redirects disabled. Both the raw
and browser-normalized `acti-sync://launch/?ticket=...` forms are accepted;
arbitrary paths, additional queries and malformed tickets are rejected.

The fixed named pipe is authenticated by Windows SID. The user component checks
that the server PID belongs to the SCM-registered ACTISync service before sending
the ticket. The service rejects network clients and separates users by SID.
Workspace ACLs admit only the owning user, LocalService, SYSTEM and administrators;
credentials/state/snapshots are inaccessible to other interactive users. Tokens
use DPAPI in the LocalService user context. Persistent sessions bind to the
configured origin; changing origin never forwards an old queue to the new server.

Managed data lives under `%ProgramData%\ACTI\Sync`. A watcher is a latency hint;
periodic reconciliation hashes the exact working DOCX. Snapshots wait for stable
metadata, verify the opened handle's final path and single hardlink count, read
with Word-compatible sharing, validate the complete private DOCX and compare
independent hashes before publication. Safe-save replacements are rechecked.
Local locks defer capture while heartbeats continue. Temp/owner files are ignored.

Snapshots and queue state are flushed before network upload. Retries preserve
the uncertain request's idempotency key. Two pending snapshots coalesce later
saves while retaining the in-flight bytes; per-user snapshots are bounded to
1 GiB with a 256 MiB free-space guard. Rejections and conflicts retain both the
working copy and pending bytes. The tray offers retry, open local file, save copy
and finish. Finish refuses uncaptured, unstable or unacknowledged bytes; close
Word before finishing. The browser polls every seven seconds and suppresses a
saved label when contact or lease is stale.

## Build and deployment

Use Windows x64, pinned .NET SDK **10.0.402** and WiX **6.0.2**. Dependencies are
pinned in the project files. The build script runs the Windows checks, publishes
both self-contained single-file EXEs and builds a per-machine MSI:

```powershell
./clients/acti-sync/packaging/build.ps1
```

Outputs are `clients/acti-sync/artifacts/ACTISync-1.0.2-win-x64.msi`,
`Service/ACTISync.Service.exe`, `User/ACTISync.User.exe`, and `manifest.json` with
SHA256, size and signature status. The `ACTI Sync` GitHub workflow publishes the
same outputs as a downloadable artifact for its exact commit.

The installer registers automatic service startup/recovery, the protocol handler
and user startup at sign-in. Initial install requires machine administrator/GPO;
normal tray, protocol and Word operation use the user's existing rights:

```powershell
msiexec /i ACTISync-1.0.2-win-x64.msi /qn /norestart SERVERORIGIN=https://acti.example.org /l*v install.log
msiexec /i ACTISync-1.0.2-win-x64.msi /qn /norestart /l*v upgrade.log
msiexec /x ACTISync-1.0.2-win-x64.msi /qn /norestart /l*v uninstall.log
```

An upgrade reuses the configured origin unless explicitly changed. Close the tray
before a managed upgrade. Uninstall/upgrade stop the service and deliberately
preserve managed data, including unsubmitted working copies and pending state.
Reinstall against the same origin restores state; if authorization/lease expired,
sign into ACTI and launch the same original again. A changed server revision
requires explicit conflict resolution and saving a separate copy. Do not delete
the data directory to resolve an error. Export pending user files before any
operator-directed cleanup. There is no automatic history retention deletion.

Without an Authenticode certificate the result is explicitly unsigned. For an
available signing certificate in CurrentUser\My, pass
`-SignCertificateThumbprint <thumbprint> -TimestampUrl <trusted timestamp URL>`.
The script signs EXEs before packaging and then signs/verifies the MSI. Deploy
under existing AppLocker/Defender/GPO policy; no policy bypass is supplied.

Backend deployment requires the additive migration before exposing the UI. This
PR does not authorize production migration or fleet deployment. Reboot, Office
2021 edition/bitness acceptance and rollout readiness must be distinguished from
automated tests and from testing another installed Word edition.


## Verification on 2026-10-19

A disposable ACTI database/object directory and loopback HTTPS endpoint were used;
production services and legacy sources were not migrated or modified. The local
computer runs Windows 11 LTSC x64 with Microsoft 365 Word x64 (16.0.20430).
This is an actual Word test, **not Word 2021 acceptance**.

Observed results:

- Self-contained Service/User EXEs and a real per-machine MSI were built. Silent
  install registered SCM automatic startup under LocalService, service recovery,
  the protocol handler and sign-in startup. Upgrade 1.0.0 -> 1.0.1 returned 0 and
  retained the working DOCX and persisted state.
- An actual browser launch reached the installed client and Word. The pilot found
  Chrome's normalized URI form; the strict parser was corrected and tested.
- Native Word FileSave changed the managed DOCX. The server acknowledged revision
  2; downloaded bytes matched the saved working copy. Later offline FileSave,
  service restart, MSI uninstall/reinstall and reconnection preserved the same
  pending idempotency key and produced acknowledged revision 3. Stable local bytes,
  server download and ACK hash matched. Reopening the downloaded DOCX in real Word
  contained the latest offline edit.
- The document retained one visible original, three binary revisions, its original
  stable file ID and unchanged card metadata revision. An attempted second client
  was rejected with 409 `original_locked`. Finish released the acknowledged lease.
- The user component and Word were additionally launched under an actual restricted
  Medium-integrity Windows token with Administrators disabled. The service
  authenticated IPC, downloaded the DOCX and confirmed Word opened it. This used
  the existing user's SID with reduced rights, not a new Windows account.
- The browser displayed committed status/history, preserved a deliberately dirty
  form across polling and retained existing view/download controls. The pilot
  corrected a header wrapping regression. Deliberate connection outages caused
  expected HTTP failures; no application script exception was observed.
- Backend PostgreSQL tests exercise replay, concurrent exchange/save, lease/CAS,
  rollback cleanup, legacy links, migration backfill, current search, logout,
  blocked accounts, viewer and field ACLs. Windows checks cover actual NTFS
  snapshots/reparse/hardlinks, held Word writer/byte-range locks, safe-save replace,
  durable retry/restart, origin changes, conflicts and refusing unsafe finish.
  Frontend checks cover current originals, status freshness, manual versions,
  readers, dirty forms and existing workspace modes. The CI command lists are in
  `.github/workflows/acti-sync.yml`.

The first hosted frontend check found a real non-DOCX control regression. The
Sync renderer is now limited to DOCX and the existing named/versioned download
control remains for other types. Its failing test passed after the fix. The
Windows pilot also exposed and corrected live-Word sharing, local-lock heartbeat,
uncaptured-finish and early hidden-window IPC dispatch defects.

Remaining acceptance limits: Word **2021** is not installed on this computer;
Office x86, a real PC reboot, GPO fleet deployment and trusted production signing
were not tested. The available local signing certificate does not have a trusted
chain, so published artifacts are explicitly unsigned. The real Word save was
invoked through Word's native FileSave command; a physical Ctrl+S keypress was not
claimed. No fleet/Word-2021 READY declaration is made. Complete the Word 2021
edition/bitness and reboot pilot before a broader rollout.
