# Agent roles implementation plan

Baseline: `cb6e8ec` (`Add context tracking and parallel agent delegation`).

## Stages and gates

1. **Schema/migration/backups** — structured role state (not Markdown-only), schema_version=2; legacy v1→v2 migration makes a backup, writes atomically, and restores/rolls back on failure. Gate: isolated migration tests cover default role, backup, atomicity and rollback.
2. **Versioned roles/prompts/snapshots** — role catalog and per-chat snapshot contain role_id, role_version, prompt_revision and policy_version. Gate: isolated persistence/reload tests.
3. **Deny-first authorization/capabilities** — title is display-only; child capabilities are a subset of parent capabilities intersected with delegated capabilities. Gate: positive and negative authorization tests.
4. **Inspector/UI** — UI displays agent name, role, model and capabilities. Gate: UI tests verify rendering and no privilege implied by title.
5. **Delegation/async/audit** — async correlation is based on stable request/chat IDs rather than mutable current selection; idempotency, interrupted recovery and audit are covered. Gate: parallel delegation, duplicate delivery and recovery tests.
6. **Integration/migration/tests/clean build/acceptance** — full suite, isolated acceptance, clean `dist-test` EXE, static/package checks, then commit and ordinary push. No release. Gate: all prior gates and acceptance pass.

Security rules: deny first; titles never identify authority; secrets and user data stay out of commits and artifacts; unrelated local changes are preserved.
