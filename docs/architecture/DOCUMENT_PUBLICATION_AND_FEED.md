# Document publication and continuous results

## Agreed behavior

The card lifecycle is separate from a legal act's configured status. There are two card states: `draft` (Черновик) and `published` (Опубликован; saving an official card has this same meaning). New cards default to draft. Publication targets are independent checkboxes: `internal` (Внутренняя база, selected by default) and `shared` (Общая база, explicit opt-in).

Only admin/editor roles may retrieve non-shared or draft cards. Readers, including the non-editor `user` role, see published cards with the shared target only. Existing registry grants, per-document ACL, field redaction and download restrictions remain additional requirements. Client-supplied editor mode cannot widen a reader's access. This applies to counts, search, export, direct cards, history, original files and derived content through the canonical document policy.

Configured number-series bindings reserve a number for draft cards. Publication finalizes the same reservation, without issuing a replacement number. Existing assigned and legacy numbers remain protected. A published card cannot return to draft; removing the shared publication target revokes reader visibility without rewriting originals or legal status. Unconfigured number series are not invented and controlled historical-number issuance remains blocked.

Controlled legacy import initializes cards as published and targets the internal base. Shared publication is never inferred from legacy publication fields. SQL Server and original source files remain read-only; this task changes the import contract/default mapping, not execution of the real corpus import. Existing target records with source-qualified legacy identity receive the same default; unrelated target cards remain private by default. Historical metadata revisions without lifecycle evidence remain explicitly unknown.

The Documents UI places saved filters below search controls, a registry dropdown beside sorting, and creation beside the layout switch (also available with All registries selected when a create-capable registry exists). Reader status filters cannot reveal drafts. Result batches append on scrolling inside the list pane, using the existing opaque cursor API. Query/filter/sort changes reset the feed and abort old requests; switching layouts preserves rows, selection, draft and scroll. No page buttons or page counters remain.

## Implementation plan

1. Add separate card state/publication-target fields and nullable historical snapshots with an additive Alembic migration; extend create/update/output/search contracts. Preserve existing document status and immutable published schemas.
2. Enforce visibility through the centralized SQL document predicate and direct-document checks. Add backend tests for both roles/modes, status filters, direct access, history/files/content, ACL revocation and legacy import defaults.
3. Integrate draft number reservations and publication finalization through the existing locked number book, with transactional/idempotent behavior and tests.
4. Implement the publication controls, compact toolbar/creation flow and cancellable infinite feed in React. Add behavioral tests for appending, reset/races, no duplicate loads, state preservation and create access.
5. Run relevant/full backend and frontend checks against owned test resources, review the complete diff, deploy the committed release with a verified backup and explicit migration, and validate editor/reader workflows in a real browser. Never modify the user-owned AGENTS.md change.

## Interfaces and ownership

API fields: `card_state: draft | published`; `publication_targets: (internal | shared)[]`, default `[internal]`. Update fields are optional and preserve current values when omitted. Search adds optional `card_state`; the existing `status` filter retains its legal/system-field meaning for compatibility. History adds nullable lifecycle snapshots.

Root owns models, migration, registry/content policy and contracts, document API integration, import contract and deployment. Frontend implementer owns frontend components/hooks/tests. Numbering implementer owns a focused bridge module, numbering-service integration and numbering lifecycle tests. The shared integration entry point is `sync_card_number(session, request, actor, document, schema)`, called after value validation/write and before appending the document history. It runs inside the document mutation transaction.
