Fix `_rebind_transcript()` in `/workspace/evaluation_fixture/src/session_manager.py`.

Requirements:
- When the manager points at a new transcript, the session object must
  receive the new `memory_access` and `transcript_path` too.
- Do not change the tests.
