# ACTI Sync multi-file reliability implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track evidence below.

**Goal:** Complete the five authorized ACTI Sync improvements and verify real behavior.
**Architecture:** Independent native session processing, additive browser telemetry,
explicit copy-first conflict recovery and guarded, staged native updates.
**Tech Stack:** .NET 10.0.402, net472 DTF/WiX 6.0.2, React 19.2.8, FastAPI/SQLAlchemy.
**Spec:** ../specs/2026-10-21-acti-sync-multifile-reliability.md

## Global constraints

- Preserve TLS, SID/session ACLs, CAS/idempotency, immutable versions and audit.
- Preserve all local work and Word processes; no forced close, reboot or v2 writes.
- Do not commit credentials, build artifacts, certificates or machine identities.
- Keep old clients compatible. Missing/stale queue telemetry remains unknown.
- Actual Code Signing certificate and real Word 2021 are external prerequisites.

## Tasks

- [ ] Native processing and recovery: Core/Service/User plus native tests. First
  demonstrate a slow file blocking a healthy file; then prove four-transfer cap,
  independent heartbeat/IPC and same-session serialization at 3/10/20 sessions.
  Add optional telemetry and copy-before-recover command. Prove safe-copy failure,
  Word-open refusal, preserved pending bytes, lost ACK and no arbitrary path.
- [ ] Backend/browser: office_sync_api.py, models/migration and frontend Sync
  components/API/tests. Validate telemetry limits and freshness, compact counts,
  version-gated recovery, CSRF/ACL/command binding, audit and idempotent recovery
  without changing current revision or another editor's lease. Run API and Web.
- [ ] Update lifecycle/distribution: Updater, InstallerActions, packaging,
  office_sync_distribution.py/settings/tests. Prove 0/partial/100 cohorts,
  verified fallback for legacy, bounded health failure/native rollback and native
  resume, invalid signer rejection and unchanged unsigned/RSA trust behavior.
- [ ] Integration/review: inspect complete diff, pin external-library documentation
  through Context7, run all relevant native/API/Web/build checks; resolve findings.
  Bump to 1.4.0 and create reviewed atomic Conventional Commits and a PR.
- [ ] Real acceptance: exercise owned Word documents and browser summary/recovery;
  build actual single MSI, verify signed release hash, upgrade and independent
  startup checks without losing Word state. Publish/deploy approved tested scope;
  record unavailable certificate/Word 2021 accurately.

## Review focus

- A stalled file must not cause unrelated lease expiry (native isolation tests).
- Old agents must not show zero pending or consume unsupported recovery commands.
- A lost recovery ACK must never drop the only local bytes or repeat an audit.
- A failed health check must restore previous installation without closing Word.
- Partial rollout must not accidentally offer a candidate to legacy updaters.

## Execution ledger

- Ruling: prior explicit user authorization requires continuous execution without
  intermediate design/plan approvals. This written design/plan records the scope;
  proceed in three disjoint implementation domains with independent review.
- Baseline: origin/main 3a39fdd1655ffae0b96238b06b4ee418aec49d76; own worktree clean.
- User confirmed no Code Signing certificate. Keep actual Authenticode acceptance
  open; implement its validation/publication controls without fabricating trust.
- GitNexus tools unavailable in this session. Verify dynamic contracts in source/tests.
