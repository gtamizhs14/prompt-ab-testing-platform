from pydantic import BaseModel
from typing import Optional, Dict


class PromptCreate(BaseModel):
    name: str


class PromptVersionCreate(BaseModel):
    system_prompt: str
    variables: Optional[Dict] = None
    temperature: Optional[str] = None
    max_tokens: Optional[int] = None
    few_shot_examples: Optional[list] = None
    commit_message: Optional[str] = None