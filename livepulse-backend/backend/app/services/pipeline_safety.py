"""Pipeline safety utilities.

SafeResult: structured result type that never returns None.
Ensures all pipeline steps produce valid output regardless of failure.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SafeResult:
    """Structured result from any pipeline step.

    Never returns None. Downstream always gets a valid result.
    """
    ok: bool = True
    data: Any = field(default_factory=list)
    error: str | None = None

    @classmethod
    def success(cls, data: Any = None) -> "SafeResult":
        return cls(ok=True, data=data if data is not None else [])

    @classmethod
    def failure(cls, error: str, data: Any = None) -> "SafeResult":
        return cls(ok=False, data=data if data is not None else [], error=error)

    @property
    def is_ok(self) -> bool:
        return self.ok

    def __bool__(self) -> bool:
        return self.ok
