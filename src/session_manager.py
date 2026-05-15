from __future__ import annotations

from pathlib import Path

from ulid import ULID

from .config import Config, get_system_prompt
from .memory import JsonlRandomAccess, message_to_memory_unit
from .session import Session


class SessionManager:
    def __init__(self, config: Config):
        self.config = config
        self.workdir = config.workdir
        self.session_id = str(ULID())
        self.encoded_cwd = str(Path(config.workdir).expanduser()).replace("/", "-").lstrip("-")
        self.storage_dir = Path(config.memory_compaction_path).expanduser() / self.encoded_cwd
        self.transcript_path = self.storage_dir / f"{self.session_id}.jsonl"
        self.memory_access = JsonlRandomAccess(self.transcript_path)
        self.session = Session(config, self.memory_access)

    async def init(self) -> None:
        await self.session.init()

    def _rebind_transcript(self, session_id: str) -> None:
        self.session_id = session_id
        self.transcript_path = self.storage_dir / f"{session_id}.jsonl"
        self.memory_access = JsonlRandomAccess(self.transcript_path)

        # Session keeps direct references to the transcript accessor, so resume
        # and fork must update both objects together.
        self.session.memory_acess = self.memory_access
        self.session.transcript_path = self.transcript_path

    def resume(self, session_id: str) -> None:
        self._rebind_transcript(session_id)
        if not self.transcript_path.exists():
            raise FileNotFoundError(f"Session {session_id} not found")

        # AC-8.1: per-session whitelist does not survive resume.
        self.session.policy.reset_to_defaults()

        # Use rehydration instead of a raw message load so resumed sessions can
        # recover the latest compaction summary and a small slice of recent
        # pre-boundary context.
        self.session.set_history(
            self.session.context_manager.rehydration(
                self.memory_access,
                system_prompt=get_system_prompt(
                    self.config.workdir,
                    self.config.security,
                    predict_before_call_enabled=self.config.predict_before_call_enabled,
                ),
            )
        )

    def fork(self) -> None:
        old_history = list(self.session.get_history())
        self._rebind_transcript(str(ULID()))

        # AC-8.1: per-session whitelist does not survive fork.
        self.session.policy.reset_to_defaults()

        # Re-serialize the current in-memory history into the new transcript
        # instead of copying raw bytes. That keeps the transcript index in sync
        # and avoids inheriting any stale offset metadata from the source file.
        for message in old_history:
            memory_unit = message_to_memory_unit(message, "message")
            if memory_unit is not None:
                self.memory_access.add_line(memory_unit.model_dump_json())

        self.session.set_history(old_history)
