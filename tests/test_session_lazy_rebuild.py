"""Tests for the lazy tool/embedding rebuild and audit rehydration filter (AC-7, AC-9)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

import pytest

from src.context_manager import Context_Manager
from src.memory import JsonlRandomAccess, Memory_Unit
from src.models import FunctionCall, Message, ToolCall
from src.permissions import PERMISSION_AUDIT_PREFIX, PermissionDecision
from src.session import _compute_skills_fingerprint


def test_compute_skills_fingerprint_returns_zero_for_missing_dir(tmp_path: Path) -> None:
    assert _compute_skills_fingerprint(None) == (0, 0)
    assert _compute_skills_fingerprint(str(tmp_path / "does-not-exist")) == (0, 0)


def test_compute_skills_fingerprint_changes_when_a_skill_is_added(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    initial = _compute_skills_fingerprint(str(skills))

    sub = skills / "python-best-practices"
    sub.mkdir()
    (sub / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody\n")

    after = _compute_skills_fingerprint(str(skills))
    assert after != initial
    # File count must have increased.
    assert after[1] > initial[1]


def test_rehydration_filter_skips_permission_audit_entries(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    access = JsonlRandomAccess(transcript)

    # Mix normal messages with [permission] audit entries.
    Memory_Unit_user = Memory_Unit(type="message", role="user", content="hello")
    access.add_line(Memory_Unit_user.model_dump_json())

    Memory_Unit_audit = Memory_Unit(
        type="message",
        role="system",
        content=f"{PERMISSION_AUDIT_PREFIX} allow_once read_file reason=user_choice",
    )
    access.add_line(Memory_Unit_audit.model_dump_json())

    Memory_Unit_assistant = Memory_Unit(
        type="message", role="assistant", content="reading file"
    )
    access.add_line(Memory_Unit_assistant.model_dump_json())

    Memory_Unit_tool = Memory_Unit(
        type="message",
        role="tool",
        content="README contents",
        tool_call_id="call-1",
    )
    access.add_line(Memory_Unit_tool.model_dump_json())

    manager = Context_Manager()
    restored = manager.rehydration(
        access,
        system_prompt="prompt",
        recent_tool_messages_before_boundary=1,
        recent_normal_messages_before_boundary=1,
    )

    contents = [m.content for m in restored]
    # The audit entry never leaks back into the rehydrated history.
    for content in contents:
        assert content is None or PERMISSION_AUDIT_PREFIX not in content
