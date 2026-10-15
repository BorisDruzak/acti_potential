# ACTI v3 Task 002: schema engine and workspaces

## Scope and design

Task 002 extends the Task 001 application on baseline
`c50a99e879a6dd8133bae188336144160a86b64f`. The supplied Task 002 brief is
the specification. SQL Server and source originals remain untouched; no bulk
document migration, identity-provider integration, OCR, BPM or AI is included.
The roadmap and the user's uncommitted `AGENTS.md` are preserved.

Registries select document types and grant user actions. Document types own
versioned schemas. A schema contains ordered sections and ordered stable-key
field definitions in JSONB; these are the logical FormSchema, FormSection and
FieldDefinition entities. Published snapshots are immutable. Documents keep
their original schema version. A changed schema creates a new version; existing
keys cannot change type or system binding. Copying a type creates a new identity.

Documents have UUID, registry/type/schema IDs, registration number/date, title,
status, revision, current file version and provenance. System values live only
in typed columns; custom values live only in JSONB. Metadata revisions and file
versions are independent histories. Every file has immutable opaque storage key,
name, size, MIME, SHA-256, author and timestamp through the existing StorageProvider.

All content operations require registry permissions, including administrators.
Document ACLs restrict these permissions further. Field visibility/write roles
apply to values, schemas, history and filtering. Searches/counts run after the
same SQL authorization predicate as detail reads. Declarative conditions allow
only equality, inequality, membership and presence; executable expressions are
never accepted. The renderer is shared by create, edit, view and both previews.

Existing admin/user roles and sessions survive migration; user is compatible
with editor, and editor/viewer roles are added. Administration of configuration
does not grant content access. Three routes `/admin`, `/work`, `/view` share
authentication, components and API. User creation uses an administrator-provided
temporary password, mandatory first-login password change, and session revocation
on credential reset or blocking.

## Shared implementation contracts

API prefix remains `/api/v1`. All JSON inputs reject unknown envelope properties.
IDs are UUID strings; timestamps ISO 8601. Errors use the existing envelope.
Configuration list endpoints return arrays; document listing returns
`{items,total,page,page_size}`. Registry output includes `permissions` and
`document_type_ids`. Schema objects are `{id,document_type_id,version,state,schema}`;
schema content is `{sections:[{key,title,fields:[...]}]}`. Fields use
`key,label,type,description,required,default,constraints,reference_set_id,
system,editable,modes,read_roles,write_roles,filterable,exportable,visible_when,
required_when,children`. Conditions are `{field,op,value}`. Roles/modes absent
or empty mean no additional restriction. Field types are `text,textarea,integer,
decimal,date,datetime,boolean,reference,multi_reference,user,department,document,
file,group`. Groups have one level of children. Nullable boolean is tri-state.

Reference sets use `code,name,registry_id` (nullable for shared sets); items use
`id,label,active,source_database,source_entity,source_id`. A field resolves its
reference by `reference_set_id` or `reference_code` within the document registry.
Document payload is `{registry_id,document_type_id,schema_version_id?,values,files?}`;
updates use `{revision,values,files?}`. Optional files are
`{field_key,name,mime,content_base64}` and save atomically with metadata, including
required attachments. Decoded aggregate limit: 25 MiB. Group file paths use
`group_key.row_index.child_key`. Output includes `values,schema,revision,
permissions,files` plus stable typed attributes. History is a list of metadata
snapshots with field redaction; files use their own version numbers. Binary
upload uses `POST /documents/{id}/files?filename=...&revision=...` with raw body
and Content-Type. Download is `GET /files/{id}/download`.

Configuration endpoints: GET/POST `/registries`, PATCH `/registries/{id}`;
GET/POST `/document-types`, PATCH `/document-types/{id}`, POST
`/document-types/{id}/copy`, GET/POST `/document-types/{id}/versions`, GET/PUT
`/form-schemas/{id}`, POST `/form-schemas/{id}/publish`; GET/POST `/references`,
PATCH `/references/{id}`, GET/POST `/references/{id}/items`, PATCH
`/references/{id}/items/{item_id}`. Content users may read only applicable
published schema and reference configuration. Administrative list/detail access
does not include document values.

User administration: GET/POST `/admin/users`, PATCH `/admin/users/{id}`,
POST `/admin/users/{id}/password`, POST `/users/me/password`; GET/PUT
`/admin/access/{registry_id}` with `{entries:[{user_id,permissions}]}`;
GET `/admin/audit` paginated. Document ACL GET/PUT `/documents/{id}/acl` uses
`{entries:[{user_id,permissions}]}`; any ACL entries make an explicit allowlist
intersected with registry grants. Administrator manages ACL without reading
document values. Empty ACL restores registry permissions.

## Implementation plan and verification ledger

- [x] Verify baseline, upstream, index metadata and local changes; inspect Task 001.
- [x] Schema/configuration: add models, migration, declarative validator,
  registries/references/version APIs and ACTI v2 seed. Test schema publication,
  key compatibility, all field kinds and reference isolation.
- [x] Content: document CRUD/search/ACL, revision conflicts, metadata history,
  immutable file versions and event allowlists. Test authorization/redaction on
  direct API, history, counts, filters and file access.
- [x] Identity: local account management, compatible roles, password changes,
  session revocation and registry grants; verify existing auth/bootstrap tests.
- [x] Frontend: three workspaces, visual schema builder, shared renderer,
  reference/user/access administration, paginated lists and version workflows.
  Test dynamic fields, modes, conditions and builder actions; build/typecheck.
- [ ] Integrate and independently review task requirements and security edges;
  run complete v3 backend/frontend suites, Ruff, Alembic check and diff checks.
- [ ] Commit task files only, complete Git publication workflow, back up the
  existing pilot PostgreSQL, deploy through mount-guarded systemd/Compose.
- [ ] Browser acceptance A-E: built-in ACTI v2, new type authored through UI,
  role boundaries, file/schema versions, authentication/health/persistence.

Review focus: hidden system fields must not leak in list headings or histories;
conditions must not expose hidden dependency values; inactive references remain
readable on old cards; simultaneous updates must return 409; file access must
recheck current ACLs and never accept client storage paths.

## ACTI v2 mapping

The built-in configuration has 25 business fields, six sections and ten
independent reference sets per registry. Both registries share one document type;
reference UUIDs and access grants remain separate.

| Legacy field | Universal representation |
| --- | --- |
| reg_number, reg_date, title | Typed system bindings, never duplicated in custom_data |
| type_id | Reference document_types, distinct from platform DocumentType |
| status_id | Reference status: legal-act status, distinct from lifecycle status |
| document_kind_id, signing_type_id | References document_kinds, signing_types |
| theme_id, executor_id, responsible_executor_id | References themes, executors, responsible_executors |
| pages_count, attachments_count | Nullable integers; UI defaults zero, ranges 0..9999 and 0..999 |
| should_publish | Nullable Boolean: Да=true, Нет=false, Не указано=NULL; future import retains raw-null provenance |
| published_where_id | Reference published_where |
| published_date, control_date | Optional dates; source strings/empty sentinel require controlled import validation |
| number | Publication/newspaper number text, distinct from registration number |
| removed_from_control | Nullable Boolean; UI default false; historical NULL is not inferred as false |
| execution_result | Multiline text |
| case_number, volume_number, sheets | Text, preserving identifiers and ranges |
| signers, approvers | Independent multiple references, not users or executor foreign keys |
| document_path | DocumentFile UUID relation; never a client filesystem path |
| id | Source ID scoped by source_database + source_entity; target uses UUID |
| created_at | Technical source timestamp for future import |
| row_version | Source concurrency/fingerprint bytes, not a timestamp or document history |

Reference codes map to the legacy `ref_` table with the same suffix. Each
ReferenceItem preserves source_database/source_entity/source_id/source_fingerprint;
its UUID is target_id. Renaming/deactivation preserves links, and unchanged
historical inactive selections remain valid. Equal integer IDs in different
source databases/tables are never merged by name or number.

Source review identified a legacy creation bug converting removed_from_control
to NULL as though it were a date. It is not reproduced. Uninterpretable source
dates, missing originals and unresolved raion cards remain future migration
states. Historical file versions are not invented.

Only public classification constants from the archived schema are seeded. No
live SQL Server dictionary values, documents, identities or source originals
were read or imported. Other reference sets are maintained through the web UI.
No SQL Server connection or write path is added to the runtime.

## Security and operational decisions

Permissions include the complete visibility dependency chain. A hidden condition
cannot expose a value, comparison literal or write-validation oracle. Conditional
fields are conservatively excluded from server filtering/search/sorting, and
schema responses mark them non-filterable. Protected group values survive row
edits; removal of rows holding inaccessible child values is rejected. Nested
conditions use root fields; nested repeating groups are unsupported.

Documents retain their schema version after publication of later versions.
Metadata revisions, file versions and schema versions have independent numbers.
Missing originals do not prevent metadata viewing. SHA-256 is checked before
download. Transaction rollback removes only newly written uncommitted files;
committed originals are never overwritten.

The migration preserves users and sessions, adds editor/viewer roles and a
default-false initial-password flag. Legacy user is editor-compatible. Password
changes rotate sessions; reset/blocking revoke old sessions. Database locking
protects the last active administrator during concurrent account changes.

GitNexus initially matched main and the starting SHA. Queries identified the
existing authentication, storage and audit boundaries. Feature code was checked
against source/tests because it was not indexed yet. No manual reindex/sync or
cross-repository contract was introduced.

## Executed verification and pilot acceptance

Runtime/browser results are recorded here after deployment of a committed release.
Private values, credentials and machine inventories are excluded.
