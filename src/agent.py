from pydantic import BaseModel, Field
from .session_manager import SessionManager
from .config import Config,load_config
from rich.console import Console
console=Console()
from .decorate import pc_gray,pc_blue,pc_cyan,pc_magenta

class Agent(BaseModel):
    name:str
    session:SessionManager
    def __init__(self,config:Config):
        self.session=SessionManager(config)


    def input(self):
    
    def output(self):

    async def run(self):
      

