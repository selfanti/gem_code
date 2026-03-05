import os
from dataclasses import dataclass
from typing import Optional,List
from glob import glob
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import torch
@dataclass
class Skill:
    name:str
    description:str
    content:str

async def load_skills(skills_dir:str) -> List[Skill]:
    skills=[]
    skills_dir=os.path.expanduser(skills_dir)
    if  not os.path.isdir(skills_dir):
        print(f"Skills directory '{skills_dir}' does not exist or is not a directory.")
        return skills
    
    entries=os.listdir(os.path.join(skills_dir,"skills"))
    for entry in entries:
        path = os.path.join(skills_dir,"skills", entry, "SKILL.md")
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
        if line.startswith("name:"):
            name=line[5:].strip()
        elif line.startswith("description:"):
            description=line[12:].strip()
        if name and description:
            break
    return Skill(name=name,description=description,content=content)
def format_one_skill_for_prompt(s:Skill):
    return f"""
## {s.name}
{s.description}
详细说明：
{s.content}
"""
def format_skill_for_prompt(skills:List[Skill])->str:
    if len(skills)==0:
        return ""
    sections = "\n-----\n".join(map(lambda s: f"""
## {s.name}
{s.description}
详细说明：
{s.content}
""", skills))
    return f"""
你是Gem Code CLI，拥有以下skill：
{sections}

使用说明：当用户请求与某个 skill 相关时，请参考对应的 SKILL.md 内容来指导你的回答,不要描述你要调用哪个技能。
"""
@dataclass
class SkillTool:
    name:str
    description:str

    def to_openai_function(self): 
        """转换为 OpenAI function 格式"""
        return {
            "type": "function",
            "function": {
                "name": f"skill__{self.name}",
                "description": f"{self.description}",
                "parameters": {
                "type": "object",  # 必须声明
                "properties": {},  # 空对象表示无参数
                "required": []     # 可选，表示没有必填参数
                }
                
            }
        }  
#opencode的一种实现方式，使用RAG技术判断skill和任务相关性
class SkillMatcher:
    def __init__(self, model_name='all-MiniLM-L6-v2'):
        # 自动使用 GPU（如果可用）
        self.model = SentenceTransformer(model_name, device='cuda' if torch.cuda.is_available() else 'cpu')
        self.skill_embeddings = None
        self.skills = []
    
    def index_skills(self, skills: list):
        """预计算所有 skill 的向量（只需执行一次）"""
        self.skills = skills
        descriptions = [s.description for s in skills]
        # ✅ 批量编码：一次处理所有 skills，速度提升 10-100 倍
        self.skill_embeddings = self.model.encode(
            descriptions, 
            batch_size=32,      # 根据内存调整
            show_progress_bar=True,
            convert_to_numpy=True
        )
    
    def match(self, user_input: str, threshold: float = 0.7, top_k: int = 5) -> list:
        # 编码用户输入（单次）
        input_embedding = self.model.encode([user_input], convert_to_numpy=True)
        
        # ✅ 矩阵运算计算所有相似度（向量化，无 Python 循环）
        similarities = cosine_similarity(input_embedding, self.skill_embeddings)[0]
        
        # 筛选并排序
        matches = []
        for idx, score in enumerate(similarities):
            if score > threshold:
                matches.append({
                    "skill": self.skills[idx],
                    "score": float(score)
                })
        
        return sorted(matches, key=lambda x: x["score"], reverse=True)[:top_k] #相似度大于0.7的最多五个skill被选出来