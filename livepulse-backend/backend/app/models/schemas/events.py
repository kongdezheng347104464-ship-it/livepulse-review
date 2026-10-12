"""Event schema contracts.

All pipeline modules MUST emit events conforming to these schemas.
Prevents silent schema drift between ASR, OCR, Script, Danmaku layers.

Breaking any schema = breaking the replay/drill-down/correlation system.
"""
from pydantic import BaseModel, Field
from typing import Literal, Any
from datetime import datetime


# ---- Core event types ----

class ASREvent(BaseModel):
    """Speech-to-text segment."""
    type: Literal["asr"] = "asr"
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    text: str = Field(min_length=1)
    speech_rate: float | None = None
    score: float | None = Field(default=None, ge=0, le=100)


class ScriptEvent(BaseModel):
    """Analyzed script segment."""
    type: Literal["script"] = "script"
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    text: str = Field(min_length=1)
    script_type: str = Field(pattern="^(teaching|product_intro|promotion|interaction|chitchat)$")
    categories: list[str] | None = None  # top-2
    keywords: list[str] | None = None
    score: float = Field(default=50.0, ge=0, le=100)
    analysis_tags: dict | None = None


class DanmakuEvent(BaseModel):
    """OCR + sentiment analyzed danmaku."""
    type: Literal["danmaku"] = "danmaku"
    timestamp_sec: float = Field(ge=0)
    content: str = Field(min_length=1)
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    intent: str | None = None


# ---- Insight types ----

class InsightEvidence(BaseModel):
    """Data backing a single insight."""
    metrics: dict[str, Any] = Field(default_factory=dict)
    time_window: str | None = None
    related_events: list[dict] = Field(default_factory=list)


class Insight(BaseModel):
    """Ranked + explainable insight."""
    text: str = Field(min_length=1)
    score: int = Field(ge=0, le=100)
    impact: Literal["engagement", "conversion", "rhythm", "content"]
    priority: Literal["high", "medium", "low"]
    time_range: dict | None = None  # {start_sec, end_sec}
    evidence: InsightEvidence | None = None
    related_events: list[dict] = Field(default_factory=list)


# ---- Report types ----

class InsightReport(BaseModel):
    """Complete insight report for a session."""
    summary: dict
    insights: list[Insight]
    timeline_highlights: list[dict]
    recommendations: list[dict]
    profile_snapshot: dict


# ---- Pipeline step types ----

class PipelineStepRecord(BaseModel):
    """Observability record for one pipeline step."""
    step_name: str
    step_order: int
    status: Literal["pending", "running", "done", "failed", "skipped"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    input_info: dict | None = None
    output_info: dict | None = None
    error_message: str | None = Field(None, max_length=2000)
