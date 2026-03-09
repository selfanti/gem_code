"""
Session Manager - 会话管理器

仿照 Claude Code 的模式，提供以下功能：
- 对话的初始化
- 暂停/恢复工作流
- 自动保存对话
- 加载之前对话
- Fork 会话
- 会话列表管理
"""

import os
import json
import asyncio
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Callable
from enum import Enum
from ulid import ULID

from rich.console import Console
from .config import Config, Message, get_system_prompt
from .session import Session
from .decorate import pc_gray, pc_blue, pc_cyan

console = Console()


class SessionStatus(Enum):
    """会话状态枚举"""
    ACTIVE = "active"           # 活跃状态
    PAUSED = "paused"           # 暂停状态
    SAVED = "saved"             # 已保存
    ERROR = "error"             # 错误状态


@dataclass
class SessionMetadata:
    """会话元数据"""
    session_id: str
    name: str
    created_at: str
    updated_at: str
    status: str
    workdir: str
    model: str
    message_count: int = 0
    description: str = ""
    parent_id: Optional[str] = None  # Fork 时的父会话ID
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionMetadata":
        return cls(**data)


@dataclass
class Checkpoint:
    """检查点 - 保存特定时刻的会话状态"""
    checkpoint_id: str
    session_id: str
    timestamp: str
    description: str
    messages: List[Dict[str, Any]]
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SessionManager:
    """
    会话管理器 - 管理多个会话的生命周期
    
    功能：
    1. 创建、加载、保存、删除会话
    2. 暂停/恢复工作流
    3. 自动保存机制
    4. Fork 会话
    5. 会话历史管理
    6. 检查点管理
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.workdir = config.workdir
        
        # 存储目录设置
        self.storage_dir = self._get_storage_dir(config)
        self.sessions_dir = self.storage_dir / "sessions"
        self.checkpoints_dir = self.storage_dir / "checkpoints"
        
        # 确保目录存在
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        
        # 当前活跃的会话
        self._current_session: Optional[Session] = None
        self._current_session_id: Optional[str] = None
        self._current_metadata: Optional[SessionMetadata] = None
        
        # 自动保存设置
        self._auto_save_enabled = True
        self._auto_save_interval = 30  # 每30秒自动保存
        self._auto_save_task: Optional[asyncio.Task] = None
        self._last_save_time: Optional[datetime] = None
        
        # 状态回调
        self._status_callbacks: List[Callable[[str, SessionStatus], None]] = []
        
        # 会话状态
        self._session_status: Dict[str, SessionStatus] = {}
    
    def _get_storage_dir(self, config: Config) -> Path:
        """获取存储目录"""
        if config.memory_compaction_path:
            return Path(config.memory_compaction_path).expanduser()
        
        # 默认使用 ~/.gem_code/sessions
        return Path.home() / ".gem_code" / "sessions"
    
    def _encode_workdir(self, workdir: str) -> str:
        """将工作目录编码为文件名安全格式"""
        return str(Path(workdir).expanduser()).replace("/", "-").lstrip("-")
    
    def _get_session_dir(self, session_id: str) -> Path:
        """获取特定会话的存储目录"""
        return self.sessions_dir / session_id
    
    def _get_session_metadata_path(self, session_id: str) -> Path:
        """获取会话元数据文件路径"""
        return self._get_session_dir(session_id) / "metadata.json"
    
    def _get_session_history_path(self, session_id: str) -> Path:
        """获取会话历史文件路径"""
        return self._get_session_dir(session_id) / "history.jsonl"
    
    # ==================== 会话生命周期管理 ====================
    
    async def create_session(
        self, 
        name: Optional[str] = None,
        description: str = "",
        workdir: Optional[str] = None
    ) -> str:
        """
        创建新会话
        
        Args:
            name: 会话名称（可选，默认自动生成）
            description: 会话描述
            workdir: 工作目录（可选，默认使用配置中的）
            
        Returns:
            新会话的 ID
        """
        session_id = str(ULID())
        timestamp = datetime.now().isoformat()
        
        # 生成默认名称
        if not name:
            encoded_dir = self._encode_workdir(workdir or self.workdir)
            name = f"session-{encoded_dir[:20]}-{session_id[:8]}"
        
        metadata = SessionMetadata(
            session_id=session_id,
            name=name,
            created_at=timestamp,
            updated_at=timestamp,
            status=SessionStatus.ACTIVE.value,
            workdir=workdir or self.workdir,
            model=self.config.model,
            message_count=0,
            description=description
        )
        
        # 创建会话目录
        session_dir = self._get_session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        
        # 保存元数据
        self._save_metadata(metadata)
        
        # 初始化空的历史文件
        history_path = self._get_session_history_path(session_id)
        history_path.write_text("", encoding="utf-8")
        
        # 创建 Session 实例
        session_config = Config(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
            workdir=metadata.workdir,
            skills_dir=self.config.skills_dir,
            mcp_config_path=self.config.mcp_config_path,
            memory_compaction_path=str(self.storage_dir)
        )
        
        self._current_session = Session(session_config)
        await self._current_session.init()
        
        self._current_session_id = session_id
        self._current_metadata = metadata
        self._session_status[session_id] = SessionStatus.ACTIVE
        
        # 启动自动保存
        self._start_auto_save()
        
        console.print(pc_blue(f"✓ Created new session: {name} ({session_id[:8]}...)"))
        return session_id
    
    async def load_session(self, session_id: str) -> bool:
        """
        加载已有会话
        
        Args:
            session_id: 会话ID（可以是完整ID或前缀）
            
        Returns:
            是否成功加载
        """
        # 尝试查找完整ID（支持前缀匹配）
        full_session_id = self._resolve_session_id(session_id)
        if not full_session_id:
            console.print(f"[red]Session not found: {session_id}[/red]")
            return False
        
        session_id = full_session_id
        
        # 加载元数据
        metadata_path = self._get_session_metadata_path(session_id)
        if not metadata_path.exists():
            console.print(f"[red]Session metadata not found: {session_id}[/red]")
            return False
        
        try:
            metadata = SessionMetadata.from_dict(
                json.loads(metadata_path.read_text(encoding="utf-8"))
            )
        except Exception as e:
            console.print(f"[red]Failed to load session metadata: {e}[/red]")
            return False
        
        # 如果有当前会话，先保存
        if self._current_session and self._auto_save_enabled:
            await self.save_current_session()
        
        # 清理当前会话
        await self._cleanup_current_session()
        
        # 创建新的 Session 实例
        session_config = Config(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=metadata.model or self.config.model,
            workdir=metadata.workdir,
            skills_dir=self.config.skills_dir,
            mcp_config_path=self.config.mcp_config_path,
            memory_compaction_path=str(self.storage_dir)
        )
        
        self._current_session = Session(session_config)
        await self._current_session.init()
        
        # 加载历史记录
        history_path = self._get_session_history_path(session_id)
        if history_path.exists():
            try:
                messages = self._load_history_from_file(history_path)
                # 过滤掉 system 消息，因为 Session.init 已经添加了
                self._current_session.history = [
                    Message(**msg) for msg in messages 
                    if msg.get("role") != "system"
                ]
                # 重新添加 system prompt 到开头
                self._current_session.history.insert(
                    0, Message(role="system", content=get_system_prompt())
                )
            except Exception as e:
                console.print(pc_gray(f"⚠️  Failed to load history: {e}"))
        
        self._current_session_id = session_id
        self._current_metadata = metadata
        self._session_status[session_id] = SessionStatus.ACTIVE
        
        # 更新元数据状态
        metadata.status = SessionStatus.ACTIVE.value
        metadata.updated_at = datetime.now().isoformat()
        self._save_metadata(metadata)
        
        # 启动自动保存
        self._start_auto_save()
        
        console.print(pc_blue(f"✓ Loaded session: {metadata.name} ({session_id[:8]}...)"))
        console.print(pc_gray(f"  Messages: {metadata.message_count}"))
        if metadata.description:
            console.print(pc_gray(f"  Description: {metadata.description}"))
        
        return True
    
    async def save_current_session(self, force: bool = False) -> bool:
        """
        保存当前会话
        
        Args:
            force: 是否强制保存（忽略自动保存设置）
            
        Returns:
            是否成功保存
        """
        if not self._current_session or not self._current_session_id:
            return False
        
        if not self._auto_save_enabled and not force:
            return False
        
        try:
            # 保存历史记录
            history_path = self._get_session_history_path(self._current_session_id)
            messages = [_message_to_dict(m) for m in self._current_session.history]
            
            with open(history_path, "w", encoding="utf-8") as f:
                for msg in messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            
            # 更新元数据
            if self._current_metadata:
                self._current_metadata.updated_at = datetime.now().isoformat()
                self._current_metadata.message_count = len(messages)
                self._save_metadata(self._current_metadata)
            
            self._last_save_time = datetime.now()
            return True
            
        except Exception as e:
            console.print(pc_gray(f"⚠️  Failed to save session: {e}"))
            return False
    
    async def delete_session(self, session_id: str, confirm: bool = True) -> bool:
        """
        删除会话
        
        Args:
            session_id: 会话ID
            confirm: 是否需要确认
            
        Returns:
            是否成功删除
        """
        full_session_id = self._resolve_session_id(session_id)
        if not full_session_id:
            console.print(f"[red]Session not found: {session_id}[/red]")
            return False
        
        session_id = full_session_id
        
        # 如果是当前会话，先清理
        if self._current_session_id == session_id:
            if confirm:
                console.print("[yellow]Cannot delete active session. Please switch to another session first.[/yellow]")
                return False
            await self._cleanup_current_session()
        
        # 删除会话目录
        session_dir = self._get_session_dir(session_id)
        if session_dir.exists():
            import shutil
            shutil.rmtree(session_dir)
        
        # 从状态字典中移除
        self._session_status.pop(session_id, None)
        
        console.print(pc_blue(f"✓ Deleted session: {session_id[:8]}..."))
        return True
    
    # ==================== 暂停/恢复工作流 ====================
    
    async def pause_session(self) -> bool:
        """
        暂停当前会话
        
        暂停后会话状态被保存，可以稍后恢复
        
        Returns:
            是否成功暂停
        """
        if not self._current_session or not self._current_session_id:
            console.print("[yellow]No active session to pause[/yellow]")
            return False
        
        # 保存当前状态
        await self.save_current_session(force=True)
        
        # 更新状态
        self._session_status[self._current_session_id] = SessionStatus.PAUSED
        if self._current_metadata:
            self._current_metadata.status = SessionStatus.PAUSED.value
            self._save_metadata(self._current_metadata)
        
        # 停止自动保存
        self._stop_auto_save()
        
        console.print(pc_cyan(f"⏸  Paused session: {self._current_session_id[:8]}..."))
        return True
    
    async def resume_session(self, session_id: Optional[str] = None) -> bool:
        """
        恢复会话
        
        Args:
            session_id: 要恢复的会话ID，None则恢复当前暂停的会话
            
        Returns:
            是否成功恢复
        """
        if session_id is None:
            session_id = self._current_session_id
        
        if not session_id:
            console.print("[yellow]No session to resume[/yellow]")
            return False
        
        # 如果当前有活跃会话且不是目标会话，先暂停
        if (self._current_session_id and 
            self._current_session_id != session_id and
            self._session_status.get(self._current_session_id) == SessionStatus.ACTIVE):
            await self.pause_session()
        
        # 加载目标会话
        return await self.load_session(session_id)
    
    # ==================== Fork 会话 ====================
    
    async def fork_session(
        self, 
        session_id: Optional[str] = None,
        name: Optional[str] = None,
        description: str = ""
    ) -> Optional[str]:
        """
        Fork 会话 - 创建当前会话的副本，保留历史但创建新ID
        
        Args:
            session_id: 要 fork 的会话ID，None则 fork 当前会话
            name: 新会话名称
            description: 新会话描述
            
        Returns:
            新会话的 ID
        """
        source_id = session_id or self._current_session_id
        if not source_id:
            console.print("[yellow]No session to fork[/yellow]")
            return None
        
        full_source_id = self._resolve_session_id(source_id)
        if not full_source_id:
            console.print(f"[red]Source session not found: {source_id}[/red]")
            return None
        
        # 加载源会话的元数据
        source_metadata_path = self._get_session_metadata_path(full_source_id)
        source_metadata = SessionMetadata.from_dict(
            json.loads(source_metadata_path.read_text(encoding="utf-8"))
        )
        
        # 创建新会话
        new_session_id = str(ULID())
        timestamp = datetime.now().isoformat()
        
        if not name:
            name = f"{source_metadata.name}-fork"
        
        new_metadata = SessionMetadata(
            session_id=new_session_id,
            name=name,
            created_at=timestamp,
            updated_at=timestamp,
            status=SessionStatus.ACTIVE.value,
            workdir=source_metadata.workdir,
            model=source_metadata.model,
            message_count=source_metadata.message_count,
            description=description or f"Forked from {source_metadata.name}",
            parent_id=full_source_id
        )
        
        # 创建新会话目录
        new_session_dir = self._get_session_dir(new_session_id)
        new_session_dir.mkdir(parents=True, exist_ok=True)
        
        # 保存新元数据
        self._save_metadata(new_metadata)
        
        # 复制历史记录
        source_history_path = self._get_session_history_path(full_source_id)
        new_history_path = self._get_session_history_path(new_session_id)
        
        if source_history_path.exists():
            import shutil
            shutil.copy2(source_history_path, new_history_path)
        
        console.print(pc_blue(f"✓ Forked session: {source_metadata.name} → {name} ({new_session_id[:8]}...)"))
        return new_session_id
    
    # ==================== 会话列表和查询 ====================
    
    def list_sessions(self, workdir: Optional[str] = None) -> List[SessionMetadata]:
        """
        列出所有会话
        
        Args:
            workdir: 可选，只列出指定工作目录的会话
            
        Returns:
            会话元数据列表（按更新时间倒序）
        """
        sessions: List[SessionMetadata] = []
        
        if not self.sessions_dir.exists():
            return sessions
        
        for session_dir in self.sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            
            metadata_path = session_dir / "metadata.json"
            if not metadata_path.exists():
                continue
            
            try:
                metadata = SessionMetadata.from_dict(
                    json.loads(metadata_path.read_text(encoding="utf-8"))
                )
                
                # 过滤工作目录
                if workdir and not metadata.workdir.startswith(workdir):
                    continue
                
                sessions.append(metadata)
            except Exception:
                continue
        
        # 按更新时间倒序排序
        sessions.sort(key=lambda x: x.updated_at, reverse=True)
        return sessions
    
    def get_session_info(self, session_id: str) -> Optional[SessionMetadata]:
        """获取会话信息"""
        full_session_id = self._resolve_session_id(session_id)
        if not full_session_id:
            return None
        
        metadata_path = self._get_session_metadata_path(full_session_id)
        if not metadata_path.exists():
            return None
        
        try:
            return SessionMetadata.from_dict(
                json.loads(metadata_path.read_text(encoding="utf-8"))
            )
        except Exception:
            return None
    
    def get_current_session_id(self) -> Optional[str]:
        """获取当前会话ID"""
        return self._current_session_id
    
    def get_current_session(self) -> Optional[Session]:
        """获取当前会话实例"""
        return self._current_session
    
    # ==================== 自动保存机制 ====================
    
    def _start_auto_save(self) -> None:
        """启动自动保存任务"""
        if self._auto_save_task is not None:
            return
        
        self._auto_save_enabled = True
        self._auto_save_task = asyncio.create_task(self._auto_save_loop())
    
    def _stop_auto_save(self) -> None:
        """停止自动保存任务"""
        if self._auto_save_task is not None:
            self._auto_save_task.cancel()
            self._auto_save_task = None
    
    async def _auto_save_loop(self) -> None:
        """自动保存循环"""
        while self._auto_save_enabled:
            try:
                await asyncio.sleep(self._auto_save_interval)
                await self.save_current_session()
            except asyncio.CancelledError:
                break
            except Exception as e:
                console.print(pc_gray(f"⚠️  Auto-save error: {e}"))
    
    def set_auto_save(self, enabled: bool, interval: Optional[int] = None) -> None:
        """
        设置自动保存
        
        Args:
            enabled: 是否启用
            interval: 自动保存间隔（秒）
        """
        self._auto_save_enabled = enabled
        
        if interval is not None:
            self._auto_save_interval = interval
        
        if enabled:
            self._start_auto_save()
        else:
            self._stop_auto_save()
    
    # ==================== 检查点管理 ====================
    
    async def create_checkpoint(self, description: str = "") -> Optional[str]:
        """
        创建检查点 - 保存当前会话状态的快照
        
        Args:
            description: 检查点描述
            
        Returns:
            检查点ID
        """
        if not self._current_session or not self._current_session_id:
            return None
        
        checkpoint_id = str(ULID())
        timestamp = datetime.now().isoformat()
        
        messages = [_message_to_dict(m) for m in self._current_session.history]
        
        checkpoint = Checkpoint(
            checkpoint_id=checkpoint_id,
            session_id=self._current_session_id,
            timestamp=timestamp,
            description=description or f"Checkpoint at {timestamp}",
            messages=messages
        )
        
        checkpoint_path = self.checkpoints_dir / f"{checkpoint_id}.json"
        checkpoint_path.write_text(
            json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        
        console.print(pc_gray(f"💾 Checkpoint created: {checkpoint_id[:8]}..."))
        return checkpoint_id
    
    def list_checkpoints(self, session_id: Optional[str] = None) -> List[Checkpoint]:
        """
        列出检查点
        
        Args:
            session_id: 可选，只列出指定会话的检查点
            
        Returns:
            检查点列表
        """
        checkpoints: List[Checkpoint] = []
        
        if not self.checkpoints_dir.exists():
            return checkpoints
        
        for checkpoint_file in self.checkpoints_dir.glob("*.json"):
            try:
                data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                checkpoint = Checkpoint(**data)
                
                if session_id is None or checkpoint.session_id == session_id:
                    checkpoints.append(checkpoint)
            except Exception:
                continue
        
        checkpoints.sort(key=lambda x: x.timestamp, reverse=True)
        return checkpoints
    
    async def restore_checkpoint(self, checkpoint_id: str) -> bool:
        """
        恢复到指定检查点
        
        Args:
            checkpoint_id: 检查点ID
            
        Returns:
            是否成功恢复
        """
        checkpoint_path = self.checkpoints_dir / f"{checkpoint_id}.json"
        if not checkpoint_path.exists():
            console.print(f"[red]Checkpoint not found: {checkpoint_id}[/red]")
            return False
        
        try:
            data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint = Checkpoint(**data)
        except Exception as e:
            console.print(f"[red]Failed to load checkpoint: {e}[/red]")
            return False
        
        # 如果检查点属于不同会话，先保存当前会话
        if (self._current_session_id and 
            self._current_session_id != checkpoint.session_id):
            await self.save_current_session()
        
        # 加载检查点的会话
        if self._current_session_id != checkpoint.session_id:
            success = await self.load_session(checkpoint.session_id)
            if not success:
                return False
        
        # 恢复消息历史
        if self._current_session:
            self._current_session.history = [
                Message(**msg) for msg in checkpoint.messages
            ]
        
        console.print(pc_cyan(f"⏪ Restored checkpoint: {checkpoint.description}"))
        return True
    
    # ==================== 实用方法 ====================
    
    def rename_session(self, new_name: str) -> bool:
        """重命名当前会话"""
        if not self._current_metadata:
            return False
        
        self._current_metadata.name = new_name
        self._current_metadata.updated_at = datetime.now().isoformat()
        self._save_metadata(self._current_metadata)
        
        console.print(pc_blue(f"✓ Renamed session to: {new_name}"))
        return True
    
    def update_description(self, description: str) -> bool:
        """更新当前会话描述"""
        if not self._current_metadata:
            return False
        
        self._current_metadata.description = description
        self._current_metadata.updated_at = datetime.now().isoformat()
        self._save_metadata(self._current_metadata)
        
        return True
    
    def export_session(self, session_id: Optional[str] = None, output_path: Optional[str] = None) -> Optional[str]:
        """
        导出会话到文件
        
        Args:
            session_id: 要导出的会话ID，None则导出当前会话
            output_path: 输出文件路径
            
        Returns:
            导出的文件路径
        """
        target_id = session_id or self._current_session_id
        if not target_id:
            return None
        
        full_id = self._resolve_session_id(target_id)
        if not full_id:
            return None
        
        metadata = self.get_session_info(full_id)
        if not metadata:
            return None
        
        history_path = self._get_session_history_path(full_id)
        messages = []
        if history_path.exists():
            messages = self._load_history_from_file(history_path)
        
        export_data = {
            "metadata": metadata.to_dict(),
            "messages": messages,
            "exported_at": datetime.now().isoformat()
        }
        
        if not output_path:
            output_path = f"{metadata.name}-{full_id[:8]}.json"
        
        output_file = Path(output_path).expanduser()
        output_file.write_text(
            json.dumps(export_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        
        console.print(pc_blue(f"✓ Exported session to: {output_file}"))
        return str(output_file)
    
    async def import_session(self, file_path: str, name: Optional[str] = None) -> Optional[str]:
        """
        从文件导入会话
        
        Args:
            file_path: 导入文件路径
            name: 新会话名称（可选）
            
        Returns:
            新会话的 ID
        """
        import_file = Path(file_path).expanduser()
        if not import_file.exists():
            console.print(f"[red]File not found: {file_path}[/red]")
            return None
        
        try:
            data = json.loads(import_file.read_text(encoding="utf-8"))
            old_metadata = data.get("metadata", {})
            messages = data.get("messages", [])
        except Exception as e:
            console.print(f"[red]Failed to parse import file: {e}[/red]")
            return None
        
        # 创建新会话
        new_session_id = str(ULID())
        timestamp = datetime.now().isoformat()
        
        new_metadata = SessionMetadata(
            session_id=new_session_id,
            name=name or old_metadata.get("name", "imported-session"),
            created_at=timestamp,
            updated_at=timestamp,
            status=SessionStatus.SAVED.value,
            workdir=old_metadata.get("workdir", self.workdir),
            model=old_metadata.get("model", self.config.model),
            message_count=len(messages),
            description=old_metadata.get("description", "Imported session") + f" (from {import_file.name})"
        )
        
        # 创建会话目录和文件
        session_dir = self._get_session_dir(new_session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        
        self._save_metadata(new_metadata)
        
        history_path = self._get_session_history_path(new_session_id)
        with open(history_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        
        console.print(pc_blue(f"✓ Imported session: {new_metadata.name} ({new_session_id[:8]}...)"))
        return new_session_id
    
    # ==================== 内部辅助方法 ====================
    
    def _resolve_session_id(self, session_id: str) -> Optional[str]:
        """解析会话ID（支持前缀匹配）"""
        # 如果是完整ID且存在，直接返回
        session_dir = self._get_session_dir(session_id)
        if session_dir.exists():
            return session_id
        
        # 尝试前缀匹配
        matches = []
        for dir_path in self.sessions_dir.iterdir():
            if dir_path.name.lower().startswith(session_id.lower()):
                matches.append(dir_path.name)
        
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            console.print(f"[yellow]Multiple sessions match '{session_id}': {', '.join(m[:8] for m in matches)}[/yellow]")
            return None
        
        return None
    
    def _save_metadata(self, metadata: SessionMetadata) -> None:
        """保存会话元数据"""
        metadata_path = self._get_session_metadata_path(metadata.session_id)
        metadata_path.write_text(
            json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def _load_history_from_file(self, history_path: Path) -> List[Dict[str, Any]]:
        """从历史文件加载消息列表"""
        messages = []
        if not history_path.exists():
            return messages
        
        with open(history_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        
        return messages
    
    async def _cleanup_current_session(self) -> None:
        """清理当前会话资源"""
        if self._current_session:
            await self._current_session.cleanup()
            self._current_session = None
        
        self._current_session_id = None
        self._current_metadata = None
        self._stop_auto_save()
    
    async def cleanup(self) -> None:
        """清理所有资源（应用退出时调用）"""
        # 保存当前会话
        if self._current_session and self._auto_save_enabled:
            await self.save_current_session(force=True)
        
        # 清理会话
        await self._cleanup_current_session()
    
    def register_status_callback(self, callback: Callable[[str, SessionStatus], None]) -> None:
        """注册状态变化回调"""
        self._status_callbacks.append(callback)
    
    def _notify_status_change(self, session_id: str, status: SessionStatus) -> None:
        """通知状态变化"""
        for callback in self._status_callbacks:
            try:
                callback(session_id, status)
            except Exception:
                pass


def _message_to_dict(m: Message) -> Dict[str, Any]:
    """Convert Message to API-compatible dict format"""
    msg: Dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                }
            }
            for tc in m.tool_calls
        ]
    if m.tool_call_id:
        msg["tool_call_id"] = m.tool_call_id
    return msg
