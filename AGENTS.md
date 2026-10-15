# ACTI project rules

## Canonical repository and scope

- Canonical Git repository: https://github.com/BorisDruzak/acti_potential.git.
- Default upstream branch: `origin/main`. Verify the remote and current diff before changes.
- The local directory name `acti_potecial` is historical; the repository is `acti_potential`.
- `docs/ACTI_V3_VISION_AND_ROADMAP.md` is the current ACTI v3 product and architecture baseline.
- Everything imported from the previous application is archived in `legacy/v2/`, including its tests, installer, dependencies, and ignore rules. It is ACTI v2 reference material, not the ACTI v3 implementation.
- Do not restore the archived desktop application as the active architecture or edit it as part of v3 work unless explicitly requested.
- ACTI v3 has not been implemented yet. Infrastructure preparation is not application deployment or acceptance.

## Settings and local context

- Read `project.toml` for shared repository, layout, and storage settings.
- Before connecting to infrastructure, read the Git-ignored `PROJECT_CONTEXT.local.md` and `project.local.toml`. These identify the primary VM and the verified machine-specific state.
- The primary SSH alias is `acti-v3`. Use the existing dedicated SSH identity and strict host-key verification. Never assume another repository's host is this project's machine.
- Keep passwords, private keys, internal addresses, usernames, filesystem UUIDs, and machine-specific inventories out of tracked files. Keep private keys outside this checkout and local context ignored.
- Local Git settings under `acti.*` record the canonical repository and the primary SSH alias. Do not put passwords in Git settings.

## Storage and deployment constraints

- Keep the OS on its separate 50 GiB disk.
- Reserve the 100 GiB volume mounted at `/srv/acti/postgresql` for PostgreSQL data, including its WAL.
- Reserve the 400 GiB volume mounted at `/srv/acti/documents` for versioned document originals and associated file storage.
- Persist mounts by filesystem UUID. Require the data mounts; do not add `nofail` or use the OS filesystem as a fallback.
- Unmounted backing directories are immutable and mode `000`. Do not remove this protection during normal deployment.
- Before starting any data-writing service, verify the expected mount and UUID from local context. Native systemd services must use `RequiresMountsFor=` for their data paths; container startup must verify the mounts before binding them.
- The volumes currently belong to `root`; no database or application service account has been selected. Set ownership on the mounted volume to the actual service UID/GID during deployment. Never guess a container UID or grant `0777` permissions.
- PostgreSQL, container runtime, and ACTI services are not installed as part of disk preparation. Follow the roadmap before selecting and deploying them.
- Never repartition, reformat, move database data, or reboot as part of routine verification. New destructive changes need a separately scoped instruction.
- See `docs/INFRASTRUCTURE.md` for the shared storage contract and verified checks.

## Development workflow

- The user uses the Codex Windows app and PowerShell. Do not assume a Codex CLI installation.
- `acti_potential` is registered and indexed on the central GitNexus server. Use its repository-specific index as the canonical committed architecture baseline before broad source exploration.
- Before relying on GitNexus results, check the indexed branch and commit against the current local branch/HEAD and the relevant remote branch. An index of `main` does not include unmerged feature-branch commits. Account for uncommitted local changes separately; verify important conclusions against source and tests.
- Prefer `list_repos` and `gitnexus://repo/acti_potential/context` for index metadata, then the smallest appropriate `query`, `context`, `impact`, or `trace` operation. Do not substitute an unrelated repository's graph.
- The central updater owns normal index refreshes. Do not trigger manual reindexing or synchronization merely because a different branch or commit is indexed; first verify the branch, commit, and updater state.
- Before changing external-library behavior, inspect the pinned dependency version and consult Context7. Do not send private source or machine configuration to external documentation services.
- Preserve the roadmap unless the user explicitly requests changes to it.
- Write new text as UTF-8 without BOM. Preserve archived file contents and use Git's normal line-ending normalization when comparing blob hashes.
- For an archival-only change, verify that every original file maps to the expected preserved Git blob and that the roadmap is unchanged. Legacy tests are not v3 acceptance tests.
- Use `codex/` for new branches and Conventional Commits for commits. Do not publish changes merely to refresh an index.
