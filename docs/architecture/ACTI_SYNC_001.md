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

The domain decision of 2026-10-19 permits editing registered, published and
imported DOCX documents as **new binary revisions of the same logical original**.
Both Sync and targeted manual version upload use this policy. Every prior file,
metadata revision and import provenance remains unchanged; the current binary
pointer advances and `document.binary.saved` records the actor, original, new
immutable file and revision in audit/outbox. Card publication state, registration
and metadata are not changed by a binary save. Existing read/update/upload/download
and field ACL checks still apply to each operation; viewing mode cannot write.

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
session and opens the managed local DOCX in installed Word. Both processes work
in the background: there is no agent window, taskbar entry, tray icon or modal
agent dialog. The user's subsequent instruction supersedes the issue's original
tray requirement. Document states and recovery actions live in the browser.

The MSI pins `HKLM\SOFTWARE\ACTI\Sync\ServerOrigin` to an HTTPS origin. Neither
the protocol URI nor IPC can supply an arbitrary server or file path. Standard
certificate verification remains enabled, with redirects disabled. Both the raw
and browser-normalized `acti-sync://launch/?ticket=...` forms are accepted;
arbitrary paths, additional queries and malformed tickets are rejected.

For a privately issued server certificate, deploy the approved CA to the machine
trust store through the existing administration/GPO process: LocalService cannot
use a CA trusted only by the installing user's profile. The MSI does not bundle a
private CA or disable certificate verification.

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
working copy and pending bytes. The browser offers retry, open local file, reveal
the local copy in Explorer and finish. Word's Save As or Explorer can preserve a
separate recovery copy. Finish refuses uncaptured, unstable or unacknowledged bytes; close
Word before finishing. The browser polls every seven seconds and suppresses a
saved label when contact or lease is stale.

## Browser control and agent presence

Additive migration `0021_office_agent` adds browser-owned connections, one-use
connection tickets, command receipts and nullable observed Word open/close fields.
Apply it before deploying the background client and browser panel.

The browser's ACTI Sync panel lists the user's editing documents, their last
confirmed open/close state and independent server save acknowledgement. Closing
Word does not discard pending bytes or automatically finish a lease. A closed
document can still be waiting for synchronization. Stale presence is shown as
unknown/offline rather than fresh evidence of saving or an open Word window.

`POST /office-sync/agent/connect` issues a one-use, 60-second
`acti-sync://connect?ticket=...` link. `POST /agent/exchange` returns a restricted
connection bearer; an ordinary edit exchange also returns connection credentials.
Connection tokens authorize only heartbeat/renew and bounded command relay,
never file reading or writing. They are bound to the originating interactive
browser session, stored with DPAPI and revoked by logout, account blocking or
expiry. The configured origin remains fixed by the MSI.

The service starts automatically and the user component starts at Windows
sign-in. Pairing a new ACTI browser authentication session requires **Подключить
агент** or the first **Редактировать в Word** launch; subsequent presence and
token renewal are automatic while that session remains authorized. Closing a
browser tab does not log out; explicit logout revokes the pairing.

The user component pulses its authenticated named pipe; the service reports
presence only while that pulse is recent. This distinguishes a running service
from an available interactive component and works with no open documents.
`GET /office-sync/agent/status` supplies the browser panel. CSRF-protected
`POST /agent/commands` queues only `open`, `retry`, `reveal` or `finish` for a
specific authorized editing session. Commands expire, return acknowledged
results and cannot supply paths, executables or URLs. Dispatch rechecks the
current document ACL and the client checks the local owner SID. A retry is not
a claim that a save succeeded. Browser responses contain no local paths or
connection credentials.

## Build and deployment

Use Windows x64, pinned .NET SDK **10.0.402** and WiX **6.0.2**. Dependencies are
pinned in the project files. The build script runs the Windows checks, publishes
both self-contained single-file EXEs and builds a per-machine MSI:

```powershell
./clients/acti-sync/packaging/build.ps1
```

The single installer is `clients/acti-sync/artifacts/ACTISync-1.1.1-win-x64.msi`.
It embeds both components and needs no separate EXE or .NET runtime installation.
Internal build outputs include
`Service/ACTISync.Service.exe`, `User/ACTISync.User.exe`, and `manifest.json` with
SHA256, size and signature status. The `ACTI Sync` GitHub workflow publishes only
the MSI in its downloadable installer artifact for the exact commit.

The installer registers automatic service startup/recovery, the protocol handler
and user startup at sign-in. Initial install requires machine administrator/GPO;
normal background operation, protocol and Word use the user's existing rights:

```powershell
msiexec /i ACTISync-1.1.1-win-x64.msi /qn /norestart SERVERORIGIN=https://acti.example.org /l*v install.log
msiexec /i ACTISync-1.1.1-win-x64.msi /qn /norestart /l*v upgrade.log
msiexec /x ACTISync-1.1.1-win-x64.msi /qn /norestart /l*v uninstall.log
```

An upgrade reuses the configured origin unless explicitly changed.
An explicit `SERVERORIGIN` takes precedence over the existing registry value;
the stored value is a fallback only. Service automatic startup and the per-machine
HKLM Run entry for the background user component are installed by default, with
no separate autostart step. The user component starts at Windows sign-in.
Stop the background user component before a managed upgrade; leave Word documents intact.
Uninstall/upgrade stop the service and deliberately
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

### Headless follow-up, version 1.1.0

The local MSI was upgraded from 1.0.2 to **1.1.0** (runtime source commit
`d59c7b58e15139d7c9061c9a619d09e01ebecaf5`), exit 0. SCM remained running under
LocalService, existing working copies remained present and the installed user
process had `MainWindowHandle = 0`. No tray/window/dialog code remains in the user
component. The downloadable installer artifact now contains one MSI with both
internal EXEs embedded.

The real browser showed an online 1.1.0 agent with zero editing documents, then
one synthetic document as **Открыт в Word**. A native FileSave in installed
Microsoft 365 Word x64 running with Medium integrity produced server revision 2.
Stable working bytes, the server download and ACK hash matched; the downloaded
DOCX contained the inserted marker. The document retained one visible original
and card metadata revision 2. Closing only that saved synthetic Word document
produced **Закрыт в Word** while retaining the separate server save confirmation.
The browser's finish command reached the installed client and returned a
successful command receipt and **Сеанс завершён**. A dirty card title survived
polling, opening the panel and finishing; its original value was restored without
submitting the test change.
Stopping the interactive component made the browser report the agent offline
while SCM continued running. Restarting the windowless component restored online
presence. The panel was also rendered and inspected at a 390-pixel viewport.

For this follow-up, the browser-owned single-use tickets were passed to the known
installed handler by the pilot harness. Chrome's native external-protocol
confirmation was not automated or bypassed. The earlier browser-to-Word launch
observation above remains separate evidence. A Windows PowerShell test helper
failed to initialize with 0xc0000142 and was stopped; it produced no Word save
evidence. The successful follow-up used a separate windowless test EXE invoking
the actual Word FileSave and refusing to close unsaved documents. This helper is
not part of the installed product.

Targeted checks after the correction: **41 backend tests**, **99 frontend tests**
in nine files, Windows core/recovery/presence/command checks and self-contained
EXE/MSI build passed. Word 2021, real PC reboot, Office x86, trusted signing and
fleet/GPO rollout remain unconfirmed as stated above.

### Main deployment and MSI configuration follow-up

PR #22 was merged and its server release deployed with a verified PostgreSQL
backup and additive migrations 0020/0021. Existing document, metadata and identity
rows remained unchanged; original backfill was verified. API, worker and frontend
were healthy and the authenticated browser passed strict HTTPS, the Sync panel
and mobile rendering checks without script errors. Data and identity containers
were preserved. An initial deployment verification selected the obsolete localhost
CA for the public origin, causing a safe rollback; selecting the configured HTTPS
CA corrected the verification and activation passed without disabling TLS.

MSI **1.1.1** corrects registry search overwriting an explicitly supplied
`SERVERORIGIN`. A real 1.1.0 -> 1.1.1 upgrade honored the new configured origin,
retained working copies and installed automatic service startup plus the HKLM Run
entry. A silent repair without `SERVERORIGIN` retained the existing origin.
The installed background client confirmed online presence against the deployed
server after the already-approved CA was made available to LocalService. No
business document was edited during this server deployment verification.
Word 2021 and an actual machine reboot remain unverified.
