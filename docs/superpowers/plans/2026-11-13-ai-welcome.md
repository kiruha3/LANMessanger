# AI Welcome and Model Discovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; execute inline in the existing local branch.

**Goal:** Guide empty-chat connection setup and list catalog/reference models by sections, with live URL/key discovery.
**Architecture:** Native welcome widget inside AITranscript; embedded JSON snapshot from the user's workbook; asynchronous QtNetwork service for compatible `/models`. Existing Pi catalog remains for built-in providers.
**Tech Stack:** Python 3.11, PyQt6/QtNetwork, JSON. No workbook library in the application.
**Spec:** `docs/superpowers/specs/2026-11-13-ai-welcome-design.md`.

## Constraints and review focus

- No key-acquisition button, credentials in URLs/logs, automatic requests on typing, or invented API model IDs.
- Preserve all 28 workbook model names and numeric prices, including zero cache prices.
- Welcome is UI only; hide after first user message, return in new empty chat.
- Discovery must not freeze UI, follow redirects with credentials, accept stale responses, or disable manual model entry on failure.
- Catalog groups must not introduce nested scrolling or steal the composer.

## Tasks

- [x] Add failing synthetic API tests for authorization/path, deduped IDs/owner groups, failures, redirects, timeout and cancellation; implement `app/core/ai_catalog.py`.
- [x] Extract read-only workbook snapshot to `app/data/ai_catalog.json` using bundled Python, add task-use guidance and provenance, verify all rows and prices against workbook.
- [x] Add failing UI tests for welcome visibility, connection button, source catalog sections, discovery and manual fallback; implement `app/ui/ai_welcome.py` and integrate with AITranscript/AITab.
- [x] Add persisted reasoning levels auto/off/minimal/low/medium/high/xhigh, CLI and custom model reasoning configuration, effective Pi state display. UI and actual Pi tests observed RED then GREEN; xhigh/high/none verified in local HTTP payload.
- [x] Document connection steps and source-catalog distinction, include JSON in packaging, version 0.22.7.
- [x] Run full test suite (13 scripts), inspect synthetic welcome layout, request focused code review, fix parser recursion with a RED→GREEN regression test.
- [x] Build 0.22.7, restart when agent idle, verify responsive versioned window and packaged catalog, commit locally without push.

Review note: the effective Pi thinking label updates after startup; when selecting a new model before running it, the previous run's label remains until startup (minor, deferred).
