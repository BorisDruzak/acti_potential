# ACTI Sync automatic update acceptance, 2026-10-19

This receipt distinguishes installed/native acceptance from source tests and
publication. It covers the user's automatic update, automatic browser pairing,
separate tray and minimal document-action request. No ACTI v2 source was changed.

## Exact release and checks

- Implementation and corrections: PRs [29](https://github.com/BorisDruzak/acti_potential/pull/29),
  [30](https://github.com/BorisDruzak/acti_potential/pull/30),
  [31](https://github.com/BorisDruzak/acti_potential/pull/31),
  [32](https://github.com/BorisDruzak/acti_potential/pull/32),
  [33](https://github.com/BorisDruzak/acti_potential/pull/33),
  [34](https://github.com/BorisDruzak/acti_potential/pull/34).
- Version-only successor used to exercise replacement of both running desktop
  components: [PR 35](https://github.com/BorisDruzak/acti_potential/pull/35).
- Final installer: `ACTISync-1.3.5-win-x64.msi`, source `9c9f0f9e8a71d4275c33c14073b4fd5c4f47bfcb`.
  [Immutable release](https://github.com/BorisDruzak/acti_potential/releases/tag/acti-sync-v1.3.5-9c9f0f9e8a71).
- Final main [CI](https://github.com/BorisDruzak/acti_potential/actions/runs/39276435417)
  passed API, web, Windows and signed publication. The single MSI embeds
  Service/User/Updater/Tray self-contained EXEs and the sign-in startup entries.
- Local verification included 328 frontend tests in 41 files and a production
  build, 29 signed-feed tests, Ruff, Windows core/service/user/native-pipe checks
  and 58 updater checks. Hosted CI also built the actual installer.
- The installed protected manifest's RSA3072/SHA256 signature, source SHA,
  package size and SHA256 were independently verified. Installed registry identity,
  all four executable versions, one installed product and automatic services were
  checked. This is release-manifest signing; the MSI has no Authenticode certificate.

## Local Windows and real Word

A normal bootstrap MSI upgrade from 1.2.3 to 1.3.3 returned 0 and automatically
replaced the legacy worker. Subsequently, **1.3.3 → 1.3.4 → 1.3.5** were installed
by the continuously running updater's ordinary signed-feed polling. No installer,
worker or tray was manually started for either automatic update; the updater and
feed cache were not restarted or cleared to accelerate detection.

- Both installations returned 0 without reboot. The latest installed source is
  `9c9f0f9e8a71d4275c33c14073b4fd5c4f47bfcb`. One product remains installed; both services are automatic with
  LocalService/System identities preserved. Both User/Tray sign-in entries exist.
- The second update replaced both previously running User and Tray processes.
  The new document worker is Medium integrity; the separate tray retains the
  original desktop context. Native `Shell_NotifyIconGetRect` confirmed the actual
  notification icon. Both components have no management window.
- The same real Word process and synthetic document stayed open through both
  updates. A unique unsaved marker remained in Word with `Saved=false`, while all
  **15 existing working files** retained their bytes. The server revision and
  bytes did not change during the first update.
- Only after both updates, Word's actual `FileSave` command saved the test document.
  The server acknowledged binary revision **3**. Downloaded DOCX bytes matched
  the working file and contained the unsaved marker. Immutable original legacy DOC
  bytes, card state, metadata revision/values and metadata history were preserved;
  file history retained the original and subsequent DOCX revisions.
- The test then closed only its saved synthetic document. The server observed
  close, safely finished the session, removed the editing pencil/count and offered
  the same edit action again. The fixture was soft-deleted; versions/audit remain.

The machine has Windows 11 LTSC x64 and **Microsoft 365 Word x64, 16.0.20430**.
Word 2021 is not installed. These are real Office automation/file-save results,
**not Word 2021 acceptance**. Word 2021, a non-administrator desktop, reboot/logon
execution and broader fleet rollout remain separate acceptance environments.

## Browser and deployment

The installed bridge automatically paired after browser login without a connect
button. Editing the synthetic legacy DOC reached actual Word and created its
macro-free DOCX revision while preserving the original. The global panel showed
fresh open state and the server-confirmed save. Desktop and 390 px mobile checks
covered both document locations and the Sync panel with zero authenticated
runtime errors.

After final deployment, a separate browser context checked the user's referenced
business document read-only: both action rows have only **Редактировать в Word**
and **Скачать**, align within the viewport, and show no LibreOffice/view/manual
version buttons or guides. The uploader guide is gone. The global panel reports
the online **1.3.5** agent without a connect button. No business content or card
metadata was changed by this final UI check.

The browser acceptance used strict TLS in the existing Playwright-installed Chromium, the real
installed local bridge and authenticated ACTI APIs. Playwright MCP checked the
strict-TLS login page; the coordinated native test used the existing Node
Playwright installation to run in the same Windows user/session, use the machine's
loopback policy and coordinate real Word receipts without changing the user's tab.
No simulated Word, intercepted protocol, fake bridge or bypassed certificate check
was used. The first browser wait expired while waiting for the normal release
cache/poll interval. It continued from the same run, authentication, file/session
baseline and still-unsaved Word process; the original timeout remains recorded.

The final application deployment uses source `9c9f0f9e8a71d4275c33c14073b4fd5c4f47bfcb`. Required data mounts and
UUIDs, deployment/backup locks, a readable database backup and HTTPS readiness
were checked. API, worker and frontend are healthy with the exact source label.
Database schema and PostgreSQL/Redis/identity container identities were unchanged.
The only runtime-source changes from the previously deployed release are the last
file-upload guide removal and canonical-CSP deployment documentation. The actual
canonical response has one CSP with exactly the allowed sixteen loopback origins.

The first final-release web CI attempt had a pre-existing asynchronous Escape
assertion fail in `ServiceAccountsAdmin.test.tsx`. Its focused local file passed;
the failed hosted job was rerun on the identical source without weakening or
skipping tests. All required jobs and publication then passed in attempt 2.

## Failures found during acceptance

Published packages were not rewritten after discovery of native failures.
Versions 1.3.0–1.3.2 were withdrawn from the stable feed as prereleases. Actual
testing found historical state without `source_format`, a cross-session legacy
shutdown problem, a console helper unable to initialize under the restricted
launch, a foreign-process handle/exit race and excessive identity-query access.
These were corrected before the successful 1.3.3 bootstrap.

The machine's Explorer already runs at High integrity. A native shell probe showed
that a Medium worker could not register a notification icon on that desktop.
The separate tray uses the original logged-on user's WTS token; the document
worker remains restricted to Medium. No UAC/Explorer policy or Word privilege was
changed. The tray's fixed presence request uses Identification impersonation,
returns no document/session/command data and cannot keep a dead worker online.

The canonical HTTPS virtual host had retained an older CSP despite the application
container's new policy. The exact sixteen loopback origins were applied to that
virtual host, validated with nginx and checked on the actual canonical HTTPS
response. TLS and all other CSP directives remain enforced.

Evidence JSON, installer logs, native process receipts and desktop/mobile
screenshots remain in the ignored local acceptance directory; credentials, SIDs,
business document details and private infrastructure inventories are excluded
from this tracked report.
