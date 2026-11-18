"""Pydantic-схемы голосового помощника."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ParseIntentRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class ParseIntentResponse(BaseModel):
    intent: str
    params: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    confirm_phrase: Optional[str] = None


class AskRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    answer: str
