import os
from dataclasses import dataclass
from typing import Optional,List
@dataclass
class Skill:
    name:str
    description:str
    content:str

async def load_skills(skills_dir:str) -> List[Skill]:
    skills=[]
    if  not os.path.isdir(skills_dir):
        print(f"Skills directory '{skills_dir}' does not exist or is not a directory.")
        return skills
    
    entries=os.listdir(skills_dir)
    for entry in entries:
        path=os.path.join(skills_dir,entry,"SKILL.md")
        try:
            with open(path,'r',encoding='utf-8') as f:
                content=f.read()
                skill=parse_Skill(content)
                skills.append(skill)
        except Exception as e:
            print(f"Error loading skill from '{path}': {str(e)}")
    return skills

def parse_Skill(content:str) -> Skill:
    lines=content.splitlines()
    name=""
    description=""
    for line in lines:
        if line.startswith("# "):
            name=line[2:].strip()
        elif line.startswith("## "):
            description=line[3:].strip()
        if name and description:
            break
    return Skill(name=name,description=description,content=content)
def format_skill_for_prompt(skills:List[Skill])->str:
    if len(skills)==0:
        return ""
    sections = "\n-----\n".join(map(lambda s: f"""
## {s.name}
{s.description}
详细说明：
{s.content}
""", skills))
    return """
你是Gem Code CLI，拥有以下专业技能：
{sections}

使用说明：当用户请求与某个 skill 相关时，请参考对应的 SKILL.md 内容来指导你的回答,不要描述你要调用哪个技能。
"""