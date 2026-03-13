import json
from pathlib import Path


class JsonlRandomAccess:
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.offsets: list[int] = []
        if self.filepath.exists():
            self.rebuild_memory_index()

    def rebuild_memory_index(self) -> None:
        """Buggy implementation that seeds the first offset twice."""

        self.offsets = [0]
        if not self.filepath.exists():
            return

        offset = 0
        with open(self.filepath, "rb") as handle:
            for line in handle:
                self.offsets.append(offset)
                offset += len(line)

    def add_line(self, payload: str) -> None:
        encoded = (payload + "\n").encode("utf-8")
        with open(self.filepath, "ab") as handle:
            offset = handle.tell()
            handle.write(encoded)
        self.offsets.append(offset)

    def load_messages(self) -> list[dict]:
        messages: list[dict] = []
        with open(self.filepath, "r", encoding="utf-8") as handle:
            for offset in self.offsets:
                handle.seek(offset)
                line = handle.readline()
                if line.strip():
                    messages.append(json.loads(line))
        return messages
