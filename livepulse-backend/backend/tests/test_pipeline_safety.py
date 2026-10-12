"""Tests for SafeResult — the error propagation foundation of the entire pipeline.

All downstream services depend on SafeResult never returning None.
If SafeResult breaks, the entire pipeline silently corrupts.
"""
from app.services.pipeline_safety import SafeResult


class TestSafeResultSuccess:
    def test_success_with_data(self):
        result = SafeResult.success(["a", "b"])
        assert result.ok is True
        assert result.is_ok is True
        assert bool(result) is True
        assert result.data == ["a", "b"]
        assert result.error is None

    def test_success_with_none_data_returns_empty_list(self):
        """Critical: None input must become [] to prevent downstream .iter() crashes."""
        result = SafeResult.success(None)
        assert result.data == []
        assert result.ok is True

    def test_success_with_empty_list(self):
        result = SafeResult.success([])
        assert result.data == []
        assert result.ok is True


class TestSafeResultFailure:
    def test_failure_with_error_message(self):
        result = SafeResult.failure("OCR timeout")
        assert result.ok is False
        assert result.is_ok is False
        assert bool(result) is False
        assert result.error == "OCR timeout"
        assert result.data == []  # always gives valid empty list

    def test_failure_with_empty_error_string(self):
        result = SafeResult.failure("")
        assert result.ok is False
        assert result.error == ""

    def test_failure_with_data_preserved(self):
        result = SafeResult.failure("partial", data=[{"frame": 1}])
        assert result.ok is False
        assert result.data == [{"frame": 1}]


class TestSafeResultImmutability:
    """Each classmethod call creates independent instances."""
    def test_independent_instances(self):
        a = SafeResult.success([1])
        b = SafeResult.failure("err")
        assert a.ok is True
        assert b.ok is False
        assert a.data == [1]
        assert b.data == []
