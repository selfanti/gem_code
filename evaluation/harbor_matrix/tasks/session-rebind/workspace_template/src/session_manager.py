from dataclasses import dataclass
from pathlib import Path


@dataclass
class JsonlRandomAccess:
    filepath: Path


@dataclass
class Session:
    memory_access: JsonlRandomAccess
    transcript_path: Path


class SessionManager:
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.session_id = "initial"
        self.transcript_path = self.storage_dir / "initial.jsonl"
        self.memory_access = JsonlRandomAccess(self.transcript_path)
        self.session = Session(self.memory_access, self.transcript_path)

    def _rebind_transcript(self, session_id: str) -> None:
        self.session_id = session_id
        self.transcript_path = self.storage_dir / f"{session_id}.jsonl"
        self.memory_access = JsonlRandomAccess(self.transcript_path)
