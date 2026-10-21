# ACTI Sync multi-file reliability

The user authorized all five previously proposed improvements and verification,
with continuous execution and preservation of documents, unsaved Word changes,
immutable revisions, ACLs, CAS/idempotency, strict TLS and signed-release trust.
This is an architectural improvement of the existing ACTI Sync flow. The current
baseline is origin/main 3a39fdd1655ffae0b96238b06b4ee418aec49d76.

## Processing

Keep at most 20 unfinished files per Windows SID and a 25 MiB file limit. Replace
the global network critical section with short index access and per-session
serialization. Bound document transfers to four concurrent operations; a stalled
transfer must not block unrelated documents, IPC presence or lease heartbeats.
Maintain durable two-snapshot coalescing and exact acknowledgement validation.
Use bounded requests, independent cancellation, fair scheduling and recovery.

## Browser telemetry

Add optional `pending_uploads` (0..2) and `error_code` (a fixed safe enum) to session
heartbeats after the server advertises `queue-telemetry-v1` in session response
capabilities. Old clients/servers remain compatible and missing telemetry is unknown.
Expose scoped fresh telemetry, never local paths/tokens. Show compact counts of
open files, pending files and attention items, with freshness and concise errors.
Old/stale connections must not become confirmed saved or confirmed zero pending.

## Explicit conflict recovery

Add a browser-issued/native-executed `recover` command, version-gated to 1.4.0+.
Require the Word document to be closed before completing recovery; never save or
close Word automatically. Persist and verify a stable recovery copy and its hash
under a fixed service-owned per-session directory before any server action.
Keep the original working file and all pending snapshots. No arbitrary paths.
Native calls `POST sessions/{identity}/recover` with the bound `command_id` and
`local_sha256`. The server verifies current ACL/auth/session/connection/command,
finishes only that old session and audits the preserved hash. It never replaces
current original bytes or releases another editor's lease. Lost acknowledgements
are idempotent. The native session is finished only after server acknowledgement.
The browser can then launch the current original through the standard launch flow.

## Updates

Keep signed schema-1 release manifests and all existing asset/path/ACL checks.
Add a bounded service-start health check after StartServices and before MSI
commit: a failed check must trigger native MSI rollback. Post-install health and
User/Tray reconciliation must finish before claiming a healthy automatic update.
Word must never be a shutdown target. Add deterministic local cohort selection and
server-controlled 0..100 rollout policy bound to the verified candidate hash.
Legacy clients cannot enforce a new policy: hold them on a separately verified
fallback while rollout is below 100%; manual download may offer the candidate.
Use a capability header for new clients without sending machine/user identity.
Policies/configuration do not permit caller-selected installer targets.

Authenticode support must validate code-signing EKU, private key, expiry, chain,
timestamp and signatures on all four EXEs and the MSI before signed publication.
The user confirmed no Code Signing certificate exists. Do not generate a fake
trusted publisher or weaken certificate validation. Report actual signing as an
external remaining requirement while completing and testing its release gate.

## Verification and constraints

Run native 3/10/20-file isolation, concurrent saves, timeout, restart, quota and
recovery cases. Run real multi-document Word tests on the local computer, browser
summary/recovery tests, actual MSI builds and upgrade/startup checks. Local Office
is Microsoft 365; Word 2021 acceptance requires an actually available licensed
installation and must remain open if unavailable. No reboot or v2 writes.
Use the existing isolated worktree; preserve unrelated primary-checkout changes.
Version the new agent 1.4.0. Deploy/publish only tested artifacts, with honest
distinction between implementation, local tests, release and real acceptance.
