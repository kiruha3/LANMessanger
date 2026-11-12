# Local AI Chat Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Track completion below.

**Goal:** Build and locally package a Pi agent chat inside LAN Messenger.

**Architecture:** PyQt owns the AI tab and launches Pi RPC as a child process. A small Node helper exposes Pi account authentication to the same tab. Separate local state records each chat and its Pi session.

**Tech Stack:** Python 3.11, PyQt6, Node >=20.6.0, Pi 0.73.1, PyInstaller.

**Spec:** ../specs/2026-11-13-local-ai-chat-design.md

## Global Constraints

- No push, publication or server deployment.
- Work folder is the executable's containing directory, initial cwd for source runs, enclosing directory for macOS .app.
- Agent uses current user permissions; no automatic prompt replay after crashes.
- API keys never enter command arguments, plain settings or chat history.
- Account login, model configuration, text and tool output stay in the AI tab; OAuth may open the provider browser.
- Preserve current dist data and previous exe before rebuilding; raise app version and run the full suite first.

## Review Focus

- Fragmented UTF-8 and Unicode separators must not corrupt JSONL.
- Failed launch or rejected prompt must release the UI; cancellation must finish before another prompt.
- Process exit and application exit must stop owned child processes.
- Moving the executable must not silently continue a session in another folder.
- Key storage, user messages and tool output must stay separate; rendered content must not execute HTML.

## Task 1: Process and persistence

**Files:** app/core/ai.py, app/core/ai_auth.mjs, test_ai.py.

**Interfaces:** `application_folder() -> str`; `JsonLines.feed(data: bytes) -> list[dict]`; `AIStore(root: str)`; `PiProcess` with `received`, `error`, `busy_changed`, `send_prompt`, `abort`, `shutdown`.

- [x] Write unittest coverage for folder rules, JSONL framing, per-chat session isolation, interrupted execution, process startup/response/error/cancel using an actual fake child.
- [x] Run `py -3.11 test_ai.py` and confirm missing implementation fails.
- [x] Implement state, protected key storage, runtime discovery, RPC lifecycle and Pi account helper.
- [x] Run `py -3.11 test_ai.py`; test actual Pi discovery without sending a provider request.

## Task 2: Integrated UI

**Files:** app/ui/ai_tab.py, app/ui/main_window.py, test_ai_ui.py, scripts/run_tests.py.

**Consumes:** Task 1 process, state and account helper.

**Produces:** `AITab(parent=None, data_dir=None, work_dir=None)`; `shutdown()`.

- [x] Add a smoke test for tab navigation, auto-folder, fake streamed response, stop state and restored chat.
- [x] Confirm failure before adding AITab.
- [x] Implement chat list, connection panel, model selector, account flow, secure key option, escaped text/tool history, send and stop.
- [x] Wire navigation and application shutdown; add new tests to isolated test runner.
- [x] Run both AI tests and existing UI smoke.

## Task 3: Documentation, review and build

**Files:** app/__init__.py, README.md, CHANGELOG.md, app/ui/help_tab.py, docs/ИИ.md, requirements/build notes as needed.

- [x] Raise version to 0.22.1 and document actual setup, account/key behavior, working folder and runtime requirement.
- [x] Review full diff using a fresh reviewer, fix material findings and re-run affected checks.
- [x] Run `py -3.11 scripts/run_tests.py`, all 11 scripts green, including the actual Pi integration test.
- [x] Preserve old exe, stop running LANMessenger.exe, build with PyInstaller and auth helper as data using the clean-PATH build script.
- [x] Launch built executable, verify process stays alive, open the AI tab and load models through its packaged account helper in the native Windows UI.
- [x] Commit implementation and docs together; provide local exe and report live-provider validation limits.

## Execution decisions

- Execute in the current clean checkout on branch codex/local-ai-chat. This keeps the requested build and runtime next to existing dist data; no unrelated changes were present.
- Pi is installed as a separate runtime in dist/ai-runtime, using the machine's Node and Git Bash. The exe remains independently launchable for LAN features when Pi is missing.
- Commit feature, changelog and user docs together at the end to satisfy repository rules.
- Ruling: use `received` rather than `event` for the Qt signal; `event` shadows QObject.event and crashes the Qt dispatch loop.
- Review fix: block sending during OAuth; restore Pi messages before resuming; reject ambiguous saved-account plus API-key auth; disable Pi auto retry/compaction with acknowledged initialization commands.
- Validation: state/RPC 12 tests, UI 6 tests, real Pi against local mock provider 1 test passed. Catalog helper returned 969 models and three OAuth providers; real provider authentication was not attempted.
- Build ruling: first 0.22.0 exe failed importing QtWidgets because PyInstaller bundled 45 DLLs from unrelated Codex runtime PATH directories. Add `scripts/build_windows.ps1` with a clean PATH and bump the rebuild to 0.22.1.
- Final build: `dist/LANMessenger.exe`, 42,870,171 bytes, version 0.22.1; no unrelated runtime DLLs in Analysis. Previous executable preserved at `dist/windows-preserved/LANMessenger-before-ai-20261113.exe`.
- Native UI verification: title LAN Messenger 0.22.1, AI tab available, working folder E:/messanger/dist, catalog loaded and model picker populated. Actual external-provider requests, OAuth login and macOS build were not exercised. No push or server update.
