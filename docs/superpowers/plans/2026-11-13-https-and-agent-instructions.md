# HTTPS portable trust and agent response styles

**Goal:** fix the installer certificate error on another Windows PC, and expose two user-requested editable response styles.
**Architecture:** explicit HTTPS contexts combine native Windows chain validation (truststore) and a bundled certifi CA list. They are scoped to upstream downloads and release checks; LAN TLS stays independent. Private Node/npm use supported system CA trust. The AI tab keeps response style and two editable instruction texts, passing the selected text via Pi --append-system-prompt when starting the next task.
**Constraints:** never disable peer certificate/hostname checks; preserve keys/history; no global SSL injection, no automatic task replay; no push without user command. Windows build0.22.13; run full suite before building.

- [x] Reproduce identical issuer error with an empty SSL CA store; load certifi roots and observe verified Node download success.
- [x] TLS regressions: bundled trust without system roots, native explicit trust, reject unknown CA and wrong hostname, readable errors. Test UI output before an unfinished tool; no stream buffering defect found.
- [x] Add two styles (with/without explanations), editable instruction dialog and persisted settings; test actual Pi invocation and restore.
- [x] Verify real upstream install with HTTPS changes, docs/changelog, independent review, full suite, frozen CA contents and local executable launch.

Response-style UX was explicitly requested by the user. Defaults: with explanations asks for short user-facing updates between stages; without asks for a concise final result. Both retain tool cards and received thinking/text. Button «Инструкция…» edits the selected preset with save/cancel/reset. Changes apply to the next task; active-agent controls are disabled.

Evidence: build/https-ai-install.log reports successful real downloads, npm install
and runtime verification. build/release-0.22.13-tests.log: all17 scripts passed,
including real Pi with the custom instruction in the provider request; responses
may use role developer as well as system. Build log confirms 0.22.13; archive
inspection confirms certifi/cacert.pem, truststore._windows, and both new modules.
Independent review found no blocking defects. The failing machine itself remains
unavailable for direct validation; replace its exe and retry installation.
