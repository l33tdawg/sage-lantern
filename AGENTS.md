# AGENTS.md

## SAGE — Persistent Memory

You have persistent institutional memory via SAGE MCP.

### Boot Sequence (IMPORTANT)
1. Call `sage_inception` as your first action in every new conversation, before responding to the user
2. This loads the context stored in previous sessions, so it must run first
3. Follow the instructions returned by inception (they adapt to the user's settings)

### If SAGE MCP is not connected
Start the node: `sage-gui serve`
MCP config is in `.codex/config.toml` at project root. Restart your session after starting.
