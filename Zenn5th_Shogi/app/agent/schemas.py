"""LLMの出力スキーマ(pydantic)。ここに合わないものは弾く(04の7.3)。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CandidateMark(BaseModel):
    n: int
    reason: str


class CandidateOutput(BaseModel):
    marks: list[CandidateMark] = Field(default_factory=list)


class NarrationItem(BaseModel):
    n: int
    comment: str


class NarrationOutput(BaseModel):
    summary: str
    items: list[NarrationItem] = Field(default_factory=list)
    next_focus: list[str] = Field(default_factory=list)
