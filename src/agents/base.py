from src.llm_client import LLMClient

class Agent:
    def __init__(self, name: str, llm_client: LLMClient = None):
        self.name = name
        self.llm = llm_client

    def log(self, message):
        pass

    def call_llm(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.llm:
            raise ValueError("LLMClient is not initialized for this agent.")
        return self.llm.call(system_prompt, user_prompt)
