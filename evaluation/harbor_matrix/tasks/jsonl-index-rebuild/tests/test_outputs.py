import json

from jsonl_store import JsonlRandomAccess


def test_rebuild_memory_index_does_not_duplicate_first_line(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"content": "first"}) + "\n" + json.dumps({"content": "second"}) + "\n")

    store = JsonlRandomAccess(path)
    assert [item["content"] for item in store.load_messages()] == ["first", "second"]
