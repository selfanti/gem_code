Implement `rehydration()` in `/workspace/evaluation_fixture/src/context_manager.py`.

Requirements:
- Always start the restored history with the provided system prompt.
- Preserve the latest summary after the most recent compaction boundary.
- Keep a small slice of recent messages from before the boundary.
- Keep recent messages after the boundary.
- Do not edit the tests.
