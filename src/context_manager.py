from typing import Final
from .config import Message
from .memory import JsonlRandomAccess,Memory_Unit
from .session import Session
MAX_CONTEXT_SIZE:Final[int]=200*1000
Micro_compaction_Threshold:Final[int]=int(0.6*MAX_CONTEXT_SIZE)
Auto_compaction_Threshold:Final[int]=int(0.8*MAX_CONTEXT_SIZE)
AUTO_COMPACTION_PROMPT="""
Please summarize the conversation above into a working state that allows
continuation without re-asking questions. Include:
1. **User Intent**: What was asked for and what changed
2. **Key Technical Decisions**: Important architectural choices made
3. **Concepts Explored**: Key concepts discussed
4. **Files Touched**: Files modified and why they matter
5. **Errors Encountered**: Problems faced and how they were fixed
6. **Pending Tasks**: Outstanding tasks and their exact state
7. **Current State**: Precise description of where we are now
8. **Next Steps**: What to do next matching the most recent user intent
9. **Continuation**: Instruction to resume without asking what to do
"""
class Context_Manager():
    used_context_size:int
    def update_used_context(self,usage:int):
        self.used_context_size=usage
    def microcompaction(self,history:list[Message],memory_acess:JsonlRandomAccess):
        if self.used_context_size>Micro_compaction_Threshold:
            need_compaction=[]
            for count,message in enumerate(history):
                if message.role=="tool":
                    need_compaction.append(count)
            for index in need_compaction:
                if index<len(history)-3:
                    history[index].content=f"文件{memory_acess.filepath}中id为{history[index].id}的json格式内容"
    async def autocompaction(self,session:Session,memory_acess:JsonlRandomAccess):
        if self.used_context_size>Auto_compaction_Threshold:
            #需要对之前的所有对话进行压缩
            boder_message=Memory_Unit(type="compact_boundary")  #添加压缩边界
            memory_acess.add_line(boder_message.model_dump_json())
            compaction_result=await session.chat_one_step(user_input=AUTO_COMPACTION_PROMPT)
            summary=Memory_Unit(type="summary",content=compaction_result)
            memory_acess.add_line(summary.model_dump_json())
            session.history=session.history[:-6]




    
    

            

