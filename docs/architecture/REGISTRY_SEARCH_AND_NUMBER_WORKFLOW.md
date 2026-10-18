# Registry search and registration-number workflow

## Search and reference controls

The search timer measures an explicit search request. Five-second background
refreshes, subsequent result batches and reference-option requests preserve the
completed duration. Retrying the same query starts a new measured generation;
responses from an aborted generation cannot replace its successor.

Advanced filters come from authorized published form versions independently of
the current results and selected document type. The ACTI v2 form exposes all 24
eligible fields, including legal publication attributes. Conflicting field
definitions retain their schema-version identity. Boolean predicates distinguish
`true`, `false` and missing values. The card publication target (`internal` or
`shared`) is a separate filter. Existing server-side document and field ACLs
apply before results are returned.

Reference controls use one editable combobox with a body-level overlay. Typing
filters existing choices; at least three characters and exactly one remaining
active choice selects its UUID. Unknown text cannot become a value. Historical
inactive choices remain readable and removable. Multiple references use chips;
keyboard navigation and explicit participant creation remain supported.

All-registry filters load authorized matching books and distinguish their labels
by registry. Ordinary document forms retain registry isolation. An explicitly
configured book in an all-registry filter must occur in the authorized book list.

## Number allocation and manual entry

`POST /api/v1/registries/{registry_id}/next-number` reserves a number without
saving the form. The request identifies the document type and optional schema,
document/revision and an idempotency key. Saving a card attaches the returned
`number_entry_id` in the same transaction. Other unsaved values are preserved.

The adjacent manual-entry action allows an arbitrary valid number. Saving checks
uniqueness within the registry after trimming surrounding whitespace and
case-insensitive comparison. A duplicate returns `409 number_conflict`; the form
remains editable for correction. Different registries have independent number
namespaces. A draft's number and registration date remain editable, including
when its number is linked to a journal reservation. Replacing the number cancels
the superseded live reservation while retaining its document link, receipts,
history and occupied claim. Neither its old number nor its current number can
be assigned to another card.

New unbound cards use a dedicated non-resetting v3 series starting with decimal
ordinals and skipping all occupied keys. This sequence does not infer or continue
an unverified SQL Server counter. Its reservations do not expire, so an attached
ordinary draft remains editable after a day. Explicitly configured series retain
their existing grants and expiry policies.

Migration `0017_registry_number_claims` creates a shared namespace for cards and
number-book entries. Database triggers and registry locking protect concurrent
manual and automatic writes, including writes outside the application service.
Deleted cards, cancelled/expired/void reservations, previous manual numbers and
other series/periods retain their occupied keys. Numbers are never silently reused.

Migration preserves original cards, journal receipts, history, file metadata,
reference provenance and published schemas. Historical duplicate occupancy is
represented by one claim without rewriting its records. Active duplicate card
numbers stop migration for explicit resolution. Downgrade refuses to discard
occupied claims; after a committed migration, recovery must preserve them.

SQL Server remains read-only. This change neither imports documents nor changes
source databases or original files.

## Publication and registration locks

The card footer offers separate save and publish actions. Ordinary save retains
the stored lifecycle state. Publish validates and saves the current values,
attachments and final number, and changes the card to `published` atomically.
A failed publication leaves the draft and unsaved form editable; a later ordinary
save does not publish it. Creation offers the same explicit publication action.

After publication, the registration number and date are read-only, including
manual numbers without journal entries and empty registration fields. Other
authorized fields remain editable. API validation rejects registration changes
with `403 registration_locked`; number-book routes enforce the same restriction.
Published cards cannot return to draft.

Migration `0018_draft_registration_history` preserves existing rows and replaces
the one-entry-per-document constraint with one live reservation per document,
allowing immutable historical links. A database trigger also blocks changing
published registration fields or returning to draft through direct SQL. Downgrade
refuses to discard multiple historical links.

## Verification

Focused regressions cover explicit/background search generations, schema-scoped
filters, false versus missing publication values, combobox cancellation/scope,
manual-number correction, concurrent allocation, direct SQL guards, foreign
reservation tokens, immutable historical spelling and durable ordinary drafts.
Publication regressions also cover editable draft registration, atomic final
edits and publication, frozen published number/date, failed-publication recovery,
immutable superseded receipts and migration preservation/downgrade guards.

`scripts/e2e_search_number_workflow.mjs` runs real HTTPS browser sessions for two
editors and a viewer against an explicitly supplied disposable environment. It
checks polling duration, zero-result filter availability, overlay selection,
unsaved reservation and attachment, duplicate correction, cross-registry
uniqueness and concurrent editor requests. Its private configuration and evidence
stay outside Git. The script creates synthetic fixtures and must not run against
production.

Release acceptance additionally requires the full backend and frontend suites,
frontend build, a restore and migration rehearsal on an owned copy of a fresh
production backup, and matching remote-main and installed image revisions.
