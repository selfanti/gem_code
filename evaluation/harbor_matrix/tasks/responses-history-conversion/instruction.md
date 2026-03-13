Fix `history_to_responses_input()` in `/workspace/evaluation_fixture/src/responses_adapter.py`.

Requirements:
- Preserve assistant tool calls as `function_call` items.
- Preserve tool outputs as `function_call_output` items.
- Keep ordinary text messages in order.
