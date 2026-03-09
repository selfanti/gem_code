from typing import Final
from .config import Message
MAX_CONTEXT_SIZE:Final[int]=200*1000
Micro_compaction_Threshold:Final[int]=int(0.6*MAX_CONTEXT_SIZE)
Auto_compaction_Threshold:Final[int]=int(0.8*MAX_CONTEXT_SIZE)

class Context_Manager():
    used_context_size:int
    def update_used_context(self,usage:int):
        self.used_context_size=usage
    def microcompaction(self,history:list[Message]):
        if self.used_context_size>Micro_compaction_Threshold:
            need_delete=[]
            for count,message in enumerate(history):
                if message.role=="tool":
                    need_delete.append(count)
            for index in need_delete:
                if index<len(history)-3:
                    del history[index]  
    def autocompaction(self):
        if self.used_context_size>Auto_compaction_Threshold:
            

