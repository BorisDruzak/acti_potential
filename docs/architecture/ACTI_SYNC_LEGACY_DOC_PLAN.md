# ACTI Sync legacy DOC implementation plan

**Goal:** Edit existing DOC originals through installed Word while preserving the
source file, logical identity, all revisions, metadata, access controls and audit.
Input scope also includes DOCM, DOT, DOTX, DOTM, RTF and ODT; DOCX remains native.

**Design:** The immutable DOC is a conversion input, never an upload format.
Agent 1.2 downloads and verifies it into a private per-user workspace. A dedicated
Word COM instance opens that input read-only with macros and automatic link
updates disabled, converts to macro-free DOCX, then closes its conversion instance.
The user edits the resulting DOCX in normal Word. The existing durable queue,
lease, CAS and idempotent receipt path publishes new DOCX binary revisions of the
same logical original. Initial conversion itself is a new binary revision.

Old immutable download links still return DOC. Current projections and downloads
return DOCX after the first successful conversion save. Server metadata and
import provenance are unchanged. Older agents can still edit DOCX, but cannot
consume DOC launch tickets; they must upgrade to 1.2. No database migration.

The service performs no Office automation. Conversion runs only in the owning
interactive user process. It cannot supply arbitrary paths, origins or commands.
Source handles are verified and held against replacement; conversion publishes
only a validated DOCX in the same private workspace. A failed conversion retains
the source and reports an error through the browser. Existing working copies and
pending uploads must survive upgrade and restart.

## Tasks and checks

- [x] Backend: first reproduce DOC capability failure; allow Word anchors, add
  source-format negotiation and client version floor, preserve actual download
  MIME, name new revisions DOCX, record conversion audit. Test immutable source,
  published/imported metadata, history, ACL, CAS, manual targeted versions.
- [x] Windows: first reproduce failed DOC download in service checks; persist
  conversion input state, verify source signature/hash and safe workspace paths,
  add isolated macro-disabled Word conversion, preserve restart/recovery and
  background presence. Run Windows checks and build one MSI 1.2.
- [x] Browser: first reproduce missing DOC actions; enable Word capability polling,
  edit/manual DOCX version/history controls and explain conversion. Preserve
  server-authoritative rights, viewer restrictions and unsaved forms.
  Add own-session reopen, fresh pencil indicator and transition notifications.
- [x] Closure: reproduce the observed closed-Word/empty-queue lease retention;
  automatically finish through existing restrictive guard only with confirmed
  current bytes and no pending reopen. Keep pending/conflict sessions recoverable.
- [ ] Acceptance: focused API/Web/Windows checks, CI, PR/main, guarded deployment
  with verified backup and unchanged schema; real local MSI upgrade preserving
  startup/state; real Word-generated DOC fixture through installed agent, native
  save, immutable source/hash/history/audit and browser open/save/close/finish.

Word 2021 acceptance remains separate if that edition is unavailable; record the
actual installed Word edition used. Do not alter business documents for a test.
