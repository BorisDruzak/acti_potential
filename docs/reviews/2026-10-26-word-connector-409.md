# ACTI Sync 1.4.4: Word connector acceptance

## Root cause and fix

The local bridge returned HTTP 409 because the service disconnected IPC without
replying. EventPipe tracing showed a failed lazy load of `System.Security.Claims`
inside `NamedPipeServerStream.RunAsClient`, followed by `FileNotFoundException`
(`0x80070002`). The DLL was present in the single-file bundle. The first tray
request used an Identification token that could not perform that assembly load.

Commit `215c3c45567391581470211d69e43137887249b7` exercises WindowsIdentity under
the service identity before accepting IPC. Per-caller SID authentication, pipe
ACLs, Origin validation and SCM PID verification remain in place. A new cold
single-file test sends Identification first, then Impersonation; packaging runs
it before publication. It failed before the fix and passed afterwards.

## Real acceptance on 26 October 2026

An isolated internal draft was used. Existing business originals were not edited.

- Installed local canary: `1.4.4+215c3c45567391581470211d69e43137887249b7`.
- All four installed EXEs matched the verified MSI payload.
- Browser `/v1/connect`: HTTP 200 with `ok: true`, replacing HTTP 409.
- The ACTI button opened the synthetic DOCX in real Microsoft Word.
- Text was entered in Word and saved using its Save button.
- The server acknowledged binary revision 2 with no pending uploads.
- A server download contained the edit and matched the native ACK SHA-256:
  `ae7279052c7b5a537052a90b95cd703ddc23687760bf2f2ca7b214dacf5db8d0`.
- After closing Word, ACTI Sync completed the first session automatically.
- A fresh ACTI Sync session downloaded revision 2 into a new working folder;
  the edit was visible in the reopened Word document.
- Both test sessions finished with empty queues. Revision 1 remained unchanged;
  exactly two server revisions existed after the test.
- The pre-existing 42 native session/queue records and 85 workspace/snapshot
  files were preserved.

The successful browser was Chromium 156. The initial standalone Chromium 145
harness reset discovery connections; that browser was not accepted by this run.
Playwright MCP lost transport after the first launch. The actual Word window
and service state independently confirmed that launch; remaining browser checks
used standalone Playwright with the same Chromium 156. Reopening used the normal
ACTI launch ticket and local bridge, with HTTP 200. Word edits used native UI.

## Release scope

The canary used the existing packaging version override. The release change sets
both repository and packaging defaults to 1.4.4, so normal main CI publishes the
fix through the pinned RSA-signed manifest and immutable installer workflow.
No further native runtime behavior changes are included in the version bump.

The release pipeline must pass the native suites, fresh bundled regression,
MSI contract/ICE checks, frontend tests/build and backend integration checks
before publication. Website acceptance additionally requires a verified 1.4.4
manifest and downloading the installer through the ACTI download endpoint.
