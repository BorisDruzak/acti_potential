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
