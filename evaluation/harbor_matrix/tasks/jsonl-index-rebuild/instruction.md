Fix the offset rebuild logic in `/workspace/evaluation_fixture/src/jsonl_store.py`.

Requirements:
- Rebuilding the index must not duplicate the first line.
- Loading messages from an existing file should preserve order.
- Do not change the tests.
