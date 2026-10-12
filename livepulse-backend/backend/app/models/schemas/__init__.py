"""Event schema contracts.

All pipeline modules must conform to these schemas.
Import from here to validate at module boundaries.
"""
from .events import (
    ASREvent,
    ScriptEvent,
    DanmakuEvent,
    Insight,
    InsightEvidence,
    InsightReport,
    PipelineStepRecord,
)

__all__ = [
    "ASREvent",
    "ScriptEvent",
    "DanmakuEvent",
    "Insight",
    "InsightEvidence",
    "InsightReport",
    "PipelineStepRecord",
]
