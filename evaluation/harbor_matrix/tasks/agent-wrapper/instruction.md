Fix `/workspace/evaluation_fixture/src/agent.py`.

Requirements:
- Keep the wrapper small and valid.
- `init()` should forward to the wrapped session manager.
- `run()` should forward the instruction to the wrapped chat session.
- Do not edit tests.
