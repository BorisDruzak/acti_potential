# Document workflow parity and registry reference import

Date: 2026-10-18. Baseline: `e71910ac2c4e1354dc35f2b1b99c682d4c2f2ee6`.
This task implements the user's numbered response to the legacy audit and the
additional instruction to import all SQL reference data and remove synthetic
materials. It does not replace the roadmap or authorize document migration.
The user explicitly requests continuous execution without intermediate approvals.

## Intended behavior

- Open card and selected document set are independent. Checkboxes, Ctrl-click,
  Shift-click and keyboard support select rows. Right-click on an unselected row
  selects it; right-click inside the selection preserves it and opens a scoped
  context menu. The menu exports the selection, opens the card/original, copies
  basic requisites and offers permitted deletion. No quick-access collection.
- Selected export is a bounded authenticated POST; it exports exactly requested
  accessible IDs, with existing field ACL, formula safety and CSV/XLSX behavior.
  Unauthorized/missing IDs fail without silently widening the export.
- A repeatable filter editor supports up to 20 different custom fields combined
  with AND, restores all URL/saved filters and preserves existing basic filters.
- Published list-view configuration determines custom columns and order. Fallback
  columns include legal-act status separately from card publication state.
- Participants retain position and department. Registry-specific reference sets
  are created/selected explicitly; same source IDs/names in different registries
  never merge. Reference APIs expose only approved business participant metadata
  outside configuration mode, not private provenance.
- Deletion is logical, authorized by existing document update permission, revision
  checked and audited with a reason. It removes the card from normal lists,
  search, file/content reads and downstream retrieval while retaining originals,
  numbering, history and relationships. No source SQL mutation or physical purge.
- Concurrent sessions show latest editor/time, named revision history, and users
  viewing/editing the card through expiring presence. Polling refreshes clean
  cards/results without losing selection/scroll. Dirty forms retain local edits,
  display the remote editor/revision and require explicit reload before saving;
  optimistic revision checks remain authoritative.
- Word and LibreOffice links open an exact authorized immutable version through
  native URI handlers. A short-lived opaque file capability supports native app
  HEAD/GET without sharing browser credentials; every read rechecks actor access,
  user/session state, file visibility and integrity. Original files stay immutable.
- Reference import reads SQL Server using the dedicated read-only reader. A
  source-qualified, idempotent target importer preserves all rows, inactive values,
  additional source attributes and independent registry mappings. Plan/counts and
  fingerprints precede applying the exact snapshot. Actual source content stays
  outside Git. Reference-only import does not remove Task 012's document gate.
- Synthetic cleanup uses verified fixture ownership/provenance and dependency
  checks, backed up first. Names alone are insufficient evidence for deletion.
  Unknown/user-owned records and historical audit evidence remain intact.
- Configuration archives are separate reversible visibility records, introduced
  by migration 0016 after the additive document tombstones in 0015. They do not
  rewrite immutable published schemas. Lists and direct document/configuration,
  process, knowledge and execution reads honor archived registry/type scope.

## Implementation plan and ownership

1. Backend lifecycle and collaboration: `registry/models.py`, additive migration
   `0015_document_collaboration.py`, `registry/document_contracts.py`,
   `registry/documents.py`, `registry/policy.py`, `registry/document_api.py`,
   focused lifecycle/collaboration tests. Contract: summary adds nullable
   `updated_by: {id, display_name}`; history adds `created_by_name`;
   `DELETE /documents/{id}` body `{revision, reason}` returns 204;
   `GET /documents/{id}/collaboration?mode=editor|viewer` returns
   `{revision, updated_at, updated_by, participants:[{id,display_name,editing}]}`;
   `POST` same path adds `{client_id,editing}` heartbeat, same response. Presence
   is per tab with a 45-second TTL; anonymous/service clients cannot join.
2. Document UI: `DocumentResults`, presentation helpers, selection/filter/menu
   components, `Documents`, feed/collaboration hooks and tests. Consume step 1
   contracts; selected export POST `/documents/export-selection` body
   `{document_ids, format, columns?, mode}` returns the existing file response.
   Office issue POST `/files/{id}/office-open` body `{application,mode}` returns
   `{uri,expires_at}`; supported applications `word|libreoffice`.
3. Registry reference workflow: `config_api`, `ReferenceItems`, `DynamicForm`,
   reference types and tests; source snapshot target importer in `migration/`
   with dry-run/apply and synthetic fixture tests. Root owns actual private
   extraction, source/target verification and cleanup execution.
4. Integration: selected export and Office handoff backend/frontend helper,
   route security and contract updates, isolated test resources, full regression
   and real browser multi-user acceptance. Root coordinates shared interfaces.
5. Reference-only execution: read live source dictionaries and target inventory;
   verify backup and mounts; import exact planned source snapshot; verify per-set
   counts/fingerprints/idempotence and registry isolation; remove only proven
   synthetic materials after dependency inspection; record aggregate results.

Each implementation starts with a meaningful failing regression test, then code,
then a passing targeted run. Final checks: backend pytest and Ruff, Alembic
migration checks, frontend Vitest/typecheck/build, real backend/browser flows
using isolated resources, complete diff review and `git diff --check`.

## Review focus

- Right-click must not discard an already selected group or navigate away from
  unsaved edits; Shift ranges use the displayed order.
- Filter removal/restoration must retain all other values and query scope.
- Deleted/revoked cards must not leak through content, files, relation targets,
  export, summaries or native-office capability reuse.
- Concurrent mutation must not overwrite a draft or commit a stale revision;
  presence is advisory, never an edit lock.
- Repeated import and equal source IDs across databases must preserve UUIDs,
  inactive entries and user-created references. Cleanup must not infer ownership
  from names or delete fixtures still referenced by retained working records.

## Execution ledger

- Baseline: only pre-existing user-owned `AGENTS.md` is dirty. Continue in the
  existing feature checkout so its unmerged document UI remains the baseline.
  Do not stage or change that file, `legacy/v2/` or the roadmap.
- Planning: GitNexus main matches `origin/main`; branch additions verified in
  source. React 19.2.8 and SQLAlchemy 2.0 documentation consulted through Context7.
- Implemented all numbered workflow requirements above. Quick access is excluded
  as requested. Original Office files open for viewing; local changes can be
  uploaded as a new immutable version.
- Frontend verification: `npm.cmd test -- --run` passed 33 files / 211 tests;
  `npm.cmd run typecheck` and `npm.cmd run build` passed. Vite reports the existing
  main bundle size warning (approximately 513 kB uncompressed).
- Backend focused regressions cover exact selected exports, deleted document
  access and index removal, session-bound presence/native capabilities, scoped
  configuration archives, source metadata clearing, import events and standalone
  CLI model registration. Ruff passes for application, tests and importer.
- The checked knowledge-service OpenAPI now declares the nullable latest editor;
  `test_task010_service.py` passed all 9 tests after regenerating that contract.
- Fresh isolated HTTPS browser acceptance passed 10 scenarios with zero runtime
  errors, using three real authenticated role contexts. It verified selection and
  exports, loaded-page refresh/scroll, AND filters, configured columns/legal
  status, concurrent clean/dirty edits, named history/presence, original capability
  integrity/revocation, reasoned deletion and registry-specific reference creation.
- Native acceptance: Word COM confirmed the exact synthetic DOCX in Protected
  View, matching content and read-only state. LibreOffice UNO confirmed the exact
  synthetic DOCX as a read-only Writer document. No file-handler settings changed.
- Reference-only operation: all 20 SQL dictionaries were read twice consistently
  through the dedicated SELECT-only reader: 177 rows from okrug and 516 from
  raion. The verified target transaction created 669 entries and explicitly
  adopted 24 reviewed public seed entries while retaining their UUIDs. All 693
  source identities/fingerprints matched; an immediate repeat was unchanged.
  Five exact-empty source names retain the original empty value in private source
  attributes with stable qualified display labels. Backup/archive readability was
  verified, and document/file/history/number/grant/user/session fingerprints stayed
  unchanged. Source rows and personal attributes are kept outside Git.
- Cleanup rehearsal restored the verified database backup into an owned isolated
  database, applied both additive migrations and the exact reference snapshot,
  then validated the private cleanup allowlist against current row fingerprints.
  It logically deleted 827 confirmed fixture cards, archived 113 registries,
  65 types and 51 reference sets, deactivated 175 still-active fixture items out of
  212 reviewed items, and blocked 11 explicitly synthetic number series. Six
  unproven cards stayed intact. Protected history/original metadata, numbering,
  relationships, process/execution records, users/sessions and all imported source
  references were unchanged. The rehearsal database was dropped afterward.
- Main-contour activation and actual cleanup have separate guarded private
  receipts. The operator revalidates the exact runtime, mounts, reviewed rows and
  dependency closure immediately before applying the allowlist in one transaction.
  This reference-only transfer does not clear the separate document migration
  gates in Task 011/012.

## Verified reference snapshot counts

Each row below represents two independent registry-scoped reference sets. Counts
include empty-name and inactive values; zero is an explicitly verified empty set.

| Reference | okrug | raion |
| --- | ---: | ---: |
| Statuses | 6 | 7 |
| Document types | 2 | 3 |
| Signing types | 2 | 2 |
| Document kinds | 2 | 2 |
| Publication locations | 2 | 2 |
| Executors | 109 | 309 |
| Responsible executors | 0 | 46 |
| Themes | 25 | 27 |
| Signers | 2 | 9 |
| Approvers | 27 | 109 |
| Total | 177 | 516 |
