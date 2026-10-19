# ACTI-SYNC-001 implementation plan

Issue #21 is the binding specification; execute continuously without interim approval.
Base: origin/main bb8b320ce856a0740fbf6cd7dcb4d44ddf036157.

## Contracts and invariants
- Immutable DocumentFile objects; additive logical-original pointer and binary history; metadata revision unchanged by sync.
- Browser launch: POST /office-sync/launch {file_id}, returns {uri, session_id, expires_at}. URI acti-sync://launch?ticket=<43-char-token>; configured origin is never supplied by the URI.
- Exchange: POST /office-sync/exchange {ticket,client_version:"1.0.0"}; returns {session_id,token,file_id,filename,revision,sha256,expires_at,lease_expires_at}.
- Session client operations /office-sync/sessions/{session_id}/download, /heartbeat, /renew, /save, /finish use Authorization Bearer token; save uses If-Match: "<revision>", Idempotency-Key, X-Content-SHA256 and raw DOCX body. JSON wire format snake_case.
- Heartbeat JSON {state}; safe states editing/local_saved/syncing/offline/conflict/error; saved status is server authoritative. Return status includes revision,sha256,state,last_seen_at,last_saved_at,lease_expires_at. Renew rotates token.
- Browser GET /office-sync/files/{file_id}/status returns authorized state, capabilities and history; GET /office-sync/files/{file_id}/history for revisions and immutable download IDs. Backend agent may refine before Windows implementation; communicate changes immediately.
- Current original IDs remain equal to original legacy file IDs. Old /files/{id}/download remains immutable; current pointer download through session and explicit current projection.
- Revalidate browser session, current ACL/field, document validity, lease and CAS on every client operation; no generic token or insecure TLS.
- Windows uses LocalService, SID-authenticated named pipes, ACL isolated workspaces, DPAPI, atomic persistent snapshots/retry, explicit conflict recovery. Word launches in interactive user process only.
- No legacy/v2 or roadmap changes; no production migration/deployment or broad rollout. Local PC is authorized for installation/pilot.

## Tasks
1. Backend: additive migration, original projection/history/search, sessions/API, focused concurrency/ACL/replay/rollback tests.
2. Windows: .NET 10 self-contained service/user/tray/protocol, persistent queue, IPC/security, focused recovery/safe-save tests.
3. Web: eligible current DOCX action, statuses/history/fallback in card/file fields; preserve unsaved forms and navigation. Targeted tests and real browser.
4. Packaging: pinned SDK/WiX MSI + EXEs, GPO/upgrade/uninstall preservation and build workflow.
5. Integration: disposable backend test environment, local MSI installation/service/protocol and real installed Word pilot; document actual Office edition and remaining Word 2021 requirement if unavailable.
6. PR on codex/acti-sync-001 to main, artifact/check links, one final focused branch review.

## Progress
- Created isolated managed worktree; original user diff preserved.
- GitNexus configured endpoint unavailable; verify architectural conclusions against source/tests.
- Local Office reports Microsoft 365 x64, not confirmed Word 2021.

## 2026-10-19 user correction: browser control, headless agent, one installer

The Windows agent must have no window, taskbar entry, tray menu or modal agent dialogs. Word and Explorer remain normal user applications. One MSI embeds both internal EXEs; the downloadable installer artifact contains only this MSI (internal payload EXEs are not separate installation steps).

Browser control contract (all routes under /api/v1/office-sync):
- POST /agent/connect, cookie + CSRF, empty body: {uri: acti-sync://connect?ticket=<43>, expires_at}. Ticket single use, 60 seconds, originating interactive browser session.
- POST /agent/exchange {ticket,client_version}: {connection_id,token,expires_at}; connection bearer only allows presence/command relay, never document read/write. Token 15 minutes with POST /agents/{id}/renew. Browser logout/block invalidates it.
- Existing POST /exchange launch response additionally contains agent: {connection_id,token,expires_at}; launched OfficeEditSession bound to that connection. Service persists DPAPI credentials and SID association.
- POST /agents/{id}/heartbeat with connection bearer: {command_results:[{id,ok,error?}]}; returns {commands:[{id,session_id,action}]}. Heartbeats only while interactive user component is alive (IPC pulse <=15s); online fresh <=35s. Commands bounded/expiring, scoped to same browser session and bound agent/session, ACL rechecked before dispatch. Actions open,retry,finish,reveal. No arbitrary paths or executables accepted from browser.
- GET /agent/status cookie: {online,last_seen_at,client_version,connections:[{connection_id,online,last_seen_at,client_version}],documents:[{...file_status,document_open,opened_at,closed_at,connection_id,command:{id,action,state,error}|null}]}. Only originating browser session's records, current ACL enforced/filter unauthorized documents. Agent status may be online with zero documents. No local filesystem paths or tokens in browser responses.
- POST /agent/commands cookie+CSRF {session_id,action} -> {id,state:'pending'}. Only same browser session, currently connected agent, valid ACL; expire after60s. Result states pending/succeeded/failed/expired. Retries are idempotent by command id on client.
- Existing document heartbeat gains optional document_open boolean; opened_at/closed_at change only on observed transitions; server ACK remains independent. Windows observes Word owner file after confirmed opening, reports closed only after owner file disappears; pending changes continue to upload after close. No implicit discard or unsafe finish.

Execution split: backend API/models/migration/tests; Windows headless user process/presence/command relay/closed detection/tests; frontend global browser panel and file status/actions/tests; root packaging, integration, documentation and PR. Keep prior safety and durability contracts. Rebuild version1.1.0 and verify local upgrade with real Word365; Word2021 remains explicit acceptance gap.
