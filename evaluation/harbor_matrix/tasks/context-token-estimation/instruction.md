Improve token estimation in `/workspace/evaluation_fixture/src/context_usage.py`.

Requirements:
- Use a better heuristic than raw character count.
- CJK-heavy text should estimate close to one token per character.
- ASCII-heavy text should estimate closer to one token per four characters.
- Message estimates should include small framing overhead.
- Do not edit tests.
