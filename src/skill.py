from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List


@dataclass
class Skill:
    name: str
    description: str
    content: str


async def load_skills(skills_dir: str) -> List[Skill]:
    """Load local skills from `<skills_dir>/skills/*/SKILL.md`.

    The previous module imported large ML dependencies for an unfinished RAG
    matcher even though the active implementation only reads Markdown files. We
    keep the loader lightweight so startup cost matches the CLI's documented
    behavior.
    """

    skills: List[Skill] = []
    skills_dir = os.path.expanduser(skills_dir)
    skills_root = os.path.join(skills_dir, "skills")

    if not os.path.isdir(skills_root):
        return skills

    for entry in os.listdir(skills_root):
        path = os.path.join(skills_root, entry, "SKILL.md")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
            skills.append(parse_skill(content))
        except Exception as exc:
            print(f"Error loading skill from '{path}': {str(exc)}")

    return skills


def parse_skill(content: str) -> Skill:
    lines = content.splitlines()
    name = ""
    description = ""
    for line in lines:
        if line.startswith("name:"):
            name = line[5:].strip()
        elif line.startswith("description:"):
            description = line[12:].strip()
        if name and description:
            break
    return Skill(name=name, description=description, content=content)


def format_one_skill_for_prompt(skill: Skill) -> str:
    return f"""
## {skill.name}
{skill.description}
详细说明：
{skill.content}
"""


def format_skill_for_prompt(skills: List[Skill]) -> str:
    if not skills:
        return ""

    sections = "\n-----\n".join(
        f"""
## {skill.name}
{skill.description}
详细说明：
{skill.content}
"""
        for skill in skills
    )
    return f"""
你是 Gem Code CLI，拥有以下 skill：
{sections}

使用说明：当用户请求与某个 skill 相关时，一定要调用该工具获得 SKILL.md 内容来指导你的回答。
"""


@dataclass
class SkillTool:
    name: str
    description: str

    def to_openai_function(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": f"skill__{self.name}",
                "description": f"{self.description}",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                    "required": [],
                },
            },
        }
