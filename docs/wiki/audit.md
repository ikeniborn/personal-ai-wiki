# Audit Log

## Overview
The audit module is a single append-only helper, `audit/log.py::record`, that writes one `AuditLog` row capturing who did what to which target. It is called wherever the harness mutates content — notably every tool invocation and the fix/format ops — so privileged actions leave a durable trail. Rows are added but not committed by `record` itself; the owning service/job is the commit boundary. See [[db#Models and tables]], [[security#RBAC]].

## Recorded events
`record(session, *, user_id, action, target_type=None, target_id=None, meta=None)` constructs an `AuditLog` and `await session.flush()`es it — it never commits, so the entry lands inside the caller's transaction. `user_id` may be `None` for system/worker-initiated actions (the column is `ON DELETE SET NULL`), and `meta` defaults to `{}`.

- `action` — a free-form verb string. In practice it is namespaced `tool:<name>`: generic tool calls log `tool:{name}` (`harness/tools.py::run_tool`), and the maintenance ops log `tool:fix` (`harness/ops/fix.py`) and `tool:format` (`harness/ops/format.py`).
- `target_type` / `target_id` — the affected object, e.g. `"domain"` for a tool call or `"article"` for a fix.
- `meta` — a JSONB bag of context: `run_tool` stores `{"args_keys": sorted(args)}` (keys only, not values), and `fix` stores `{"issue_kind", "issue_id"}`.
- `created_at` is set by a `server_default=func.now()` timestamp.

## Persistence
Audit rows live in the `audit_log` table (`db/models.py::AuditLog`): a UUID PK, a nullable `user_id` FK to `users`, the `action` text, optional `target_type`/`target_id`, a `JSONB` `meta`, and a timezone-aware `created_at`. Because `record` only flushes, audit writes participate atomically in the same transaction as the action they describe — if the action rolls back, so does its audit entry. See [[db#Models and tables]] and the RBAC checks that gate the audited actions in [[security#RBAC]].
