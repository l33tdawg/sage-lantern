# AGENTS.md

## SAGE — Persistent Memory

You have persistent institutional memory via SAGE MCP.

### Boot Sequence (IMPORTANT)
1. Call `sage_inception` as your first action in every new conversation, before responding to the user
2. This loads the context stored in previous sessions, so it must run first
3. Follow the instructions returned by inception (they adapt to the user's settings)

### Inbox polling — never re-drain the archive
1. Poll with the recorded watermark: `sage_inbox(reply_since="<last newest_reply_completed_at>")`
2. `reply_catch_up_required: true` on a poll with **no** `reply_since` is the tool's default, not a
   finding. The archive was fully read on 10 Oct 2026 — do **not** page it back from scratch.
3. Inbound work arrives separately as `count`/`items`, so skipping the reply archive misses nothing
   actionable. Advance the watermark to the new `newest_reply_completed_at` when
   `reply_watermark_safe_to_advance` is true.

The sender-side archive is passive history, not a work queue: `retained_reply_count` is its size
and is never an unread count. The `since` boundary is inclusive, so the newest reply repeats;
deduplicate by `message_id`. Drained 10 Oct 2026, caught up on 11 Oct 2026 to that drain's boundary
(129 retained then), and again on 14 Oct 2026 (140 retained, 11 new replies, `page_truncated: false`
at the old watermark); watermark `2026-10-12T15:24:28.924Z` — a stale value only re-reads a few
replies, it cannot lose work.

### If SAGE MCP is not connected
Start the node: `sage-gui serve`
MCP config is in `.codex/config.toml` at project root. Restart your session after starting.
