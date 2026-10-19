# ACTI Sync automatic updates and minimal document UI

**Goal:** Automatically discover/connect the installed agent, deliver authenticated
in-place updates and restart its per-user component without touching Word or
discarding saved/unsaved work; expose a tray icon and two document actions.

**Authorization:** The user explicitly requests autonomous implementation through
main, deployment, a real installer and local browser/Word acceptance. No additional
design/implementation approval stops are required.

## Design and contracts

- Keep the synchronization service as LocalService. Add a dedicated, narrowly
  scoped LocalSystem updater service/executable inside the same MSI. Only the
  updater installs independently verified packages; ordinary IPC never accepts
  package paths, origins, commands or privileged installation instructions.
- Releases use RSA/SHA256 PKCS#1 v1.5 signatures. Envelope is JSON with exactly
  `payload` and `signature` (base64 strings); signature covers decoded payload
  bytes. Canonical public key lives in
  `backend/app/registry/acti_sync_release_public_key.pem`, embedded into the
  Windows updater. Private key stays outside this checkout and in a repository
  Actions secret. Payload fields: `schema`=1, `product`="ACTISync", `version`
  (three components, major 1), `source_sha` (40 hex), `filename`, `sha256`, `size`,
  `release_tag`, `published_at`. Filename is `ACTISync-{version}-win-x64.msi`;
  tag `acti-sync-v{version}-{source_sha[0:12]}`. Download URL is constructed from
  this canonical public GitHub repository/tag/filename; never supplied freely.
- Public bounded API `GET /api/v1/office-sync/distribution/latest` proxies a
  verified signed release envelope from canonical GitHub releases, with bounded
  polling/cache and last-good handling. No document/authentication data exposed.
  No database migration. Signing/publication runs only after all main CI jobs pass.
- Updater polls every five minutes (initial check at startup), rejects tampering,
  downgrade, wrong product/platform, oversized/incomplete MSI and broken TLS.
  Store protected staging/receipts under ProgramData; no fallback to business
  workspaces. Durable helper owns installation while SCM replaces the updater
  itself; fixed installed helper path and bounded arguments only. Recover an
  interrupted transaction without duplicate MSI installation or lost user resume.
- Before update, drain per-user native work through
  `ACTISync.Update.User.{sid}.{sessionId}` InOut named pipe. SYSTEM caller must be
  the SCM-reported ACTISyncUpdater PID. Request line `{"action":"prepare_update"}`;
  response `{"ready":true}` only after active native work/conversion drains.
  Stop accepting new launches, then exit the user component. Never close/quit/kill
  Word, finish document leases or delete/replace runtime workspaces. Relaunch only
  previously running installed user components in their original Windows sessions
  as the original SID using the logged-in user token. Preserve Medium Integrity.
  A legacy bootstrap must activate the replaced user executable automatically,
  with process-path/SID/creation identity guards and no Word termination.
- User-owned loopback bridge listens on the first free 127.0.0.1 port in
  43127..43142. Bounded HTTP only, exact installed HTTPS Origin, no cookies,
  CORS/PNA restricted to that Origin. Identify the connecting process using the
  TCP owner table; require its Windows TokenUser SID and Windows session to match
  this user component. Fail closed on lookup errors; never connect another user.
- `GET /v1/status` returns `{version, challenge}`; challenge is cryptographically
  random, one-use, short-lived. `POST /v1/connect` and `/v1/launch` accept only
  `{ticket, challenge}` and forward the strict existing ticket operation to the
  SCM-verified service. No arbitrary files/URLs/executables. Update drain returns
  unavailable. Root implements bridge/tray/control pipe.
- Frontend automatically discovers/authenticates the local bridge and issues a
  CSRF-protected connect ticket only when needed; bounded retry, single flight,
  cancellation on logout/unmount, no protocol launches without a user gesture.
  Editing uses the bridge and existing one-use launch API, with the existing
  external-protocol path as a user-gesture fallback. Remove separate connect button.
- Both document locations show aligned filename, `Редактировать в Word` and
  `Скачать` according to current permissions. Own active session uses the same
  edit label and existing scoped reopen command. Remove Word/LibreOffice viewing,
  manual new-version and document guide/status clutter. Preserve history/audit
  functionality and useful status/notifications in the global ACTI Sync panel.
- Tray icon reports current connectivity/update state and opens the existing ACTI
  web panel. No document-management window, no Windows save notifications.

### Desktop tray boundary

The document worker remains restricted to Medium integrity. A separate,
windowless `ACTISync.Tray.exe` owns only the notification icon, fixed ACTI link,
version and read-only presence display. It uses the original logged-on user's
WTS token without adding privileges, obtaining a linked elevated token, using
SYSTEM, or changing Explorer/UAC. This matches the desktop's existing context
and allows the icon to work even when an administrator's Explorer is already
High integrity. The tray cannot dispatch document commands or keep the worker's
presence alive. Its authenticated update-control pipe drains only the icon.

The updater tracks explicit User/Tray identities with fixed executable paths,
SID/session/creation checks, drains both, and resumes them separately. Legacy
receipts default to User; the legacy WM_QUIT helper never targets Tray. The
single MSI embeds all four EXEs and installs both per-user startup entries.

## Implementation and evidence

Implementation is complete for the four product areas below. Local checks pass:
41 frontend files / 328 tests and production build; 29 signed-feed tests with
pinned dependencies and Ruff; Windows core/service/user checks and 58 updater
checks. Combined MSI packaging and installed browser/Word acceptance are tracked
separately below, and are not implied by unit-test results.

- [x] Frontend: minimal actions/layout and local discovery helper/hook; focused
  regressions then full React tests/build and rendered desktop/mobile acceptance.
- [x] Backend/release: signed release verifier/feed, CI signing/publication and
  negative signature/cache tests. Pinned dependencies: cryptography 50.0.2,
  httpx 0.28.x, .NET SDK 10.0.402, WiX 6.0.2; consult official docs if Context7
  quota is unavailable. No private configuration in tracked files.
- [x] Windows updater: trust/download/install/recovery + same-MSI packaging and
  automatic user activation. Exercise invalid signatures and interrupted stages.
- [x] User runtime: guarded bridge, drain/control pipe and tray. Test cross-origin,
  replay, wrong SID/session and update races; keep service/DPAPI/SID guarantees.
- [x] Integration: review combined diff; run scoped API/Windows tests and full web
  tests, merge verified changes, deploy using mount/backup/rollback guards.
- [x] Publish the corrected bootstrap 1.3.3, then 1.3.4 and 1.3.5 for real automatic
  upgrade acceptance. Hold a real Word document open with an unsaved marker during
  upgrade; verify same Word process/document/unsaved text, new user process/tray,
  same working bytes/history, then save and verify server ACK and marker bytes.
- [x] Inspect browser screenshots for both file locations, automatic connection
  without a click, two actions only, no guides, correct compact layout and no
  authenticated runtime errors. Preserve the user's existing browser state.
- [x] Report exact source/artifact/CI/deployment and any remaining platform-specific
  acceptance gap. Word 2021 is not installed locally; Microsoft 365 evidence must
  not be called Word 2021 acceptance.

Installed/native/browser/deployment results and the remaining Word 2021 gap are
recorded in [the acceptance report](ACTI_SYNC_AUTOMATION_ACCEPTANCE.md).
