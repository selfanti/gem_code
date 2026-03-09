from ulid import ULID
import os
from .session import Session
from pathlib import Path
from .config import Config
import json
class SessionManager:
    def __init__(self, config: Config):
        self.config=config
        self.workdir = config.workdir
        # 生成新的 UUID v4
        self.session_id = str(ULID())
        self.encoded_cwd = str(Path(config.workdir).expanduser()).replace("/", "-").lstrip("-")
        self.storage_dir = Path(config.memory_compaction_path).joinpath(self.encoded_cwd).expanduser()
        self.transcript_path = self.storage_dir / f"{self.session_id}.jsonl"
        self.session=Session(config)
        
    def resume(self, session_id: str):
        """恢复现有会话"""
        self.session_id = session_id
        self.transcript_path = self.storage_dir / f"{session_id}.jsonl"
        # 验证文件存在性
        if not self.transcript_path.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        self.session.history=json.load(str(self.transcript_path))
    def fork(self):
        """Fork新会话（保留历史但创建新ID）"""
        old_transcript = self.transcript_path
        self.session_id = str(ULID())  # 新ID
        self.transcript_path = self.storage_dir / f"{self.session_id}.jsonl"
        # 复制旧会话内容到新文件
        if old_transcript.exists():
            content = old_transcript.read_text()
            self.transcript_path.write_text(content)