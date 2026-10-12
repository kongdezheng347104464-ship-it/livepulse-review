"""Unit tests for the ffmpeg timeout wrapper (audit 2026-06-03 P1-1).

We don't exercise real ffmpeg here — we mock subprocess.run to assert the
wrapper's three behaviors:
  1. success → returns the CompletedProcess
  2. TimeoutExpired → raises VideoProcessError (not a hang, not the raw exc)
  3. CalledProcessError → raises VideoProcessError with stderr logged

Plus _capture_frames' skip-and-continue: a single frame timeout must not
abort the whole extraction; only the step deadline is fatal.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from app.services.video_processor import _run_ffmpeg, VideoProcessError


class TestRunFfmpeg:
    def test_success_returns_completed_process(self):
        fake = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("app.services.video_processor.subprocess.run", return_value=fake) as m:
            out = _run_ffmpeg(["ffmpeg", "-i", "x"], timeout=30, desc="test")
        assert out is fake
        # timeout must be forwarded to subprocess.run
        assert m.call_args.kwargs["timeout"] == 30

    def test_timeout_raises_video_process_error(self):
        with patch(
            "app.services.video_processor.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30),
        ):
            with pytest.raises(VideoProcessError) as ei:
                _run_ffmpeg(["ffmpeg"], timeout=30, desc="视频转码")
        # message carries the human desc + mentions timeout
        assert "视频转码" in str(ei.value)
        assert "超时" in str(ei.value)

    def test_called_process_error_raises_video_process_error(self):
        err = subprocess.CalledProcessError(
            returncode=1, cmd="ffmpeg", stderr="moov atom not found",
        )
        with patch("app.services.video_processor.subprocess.run", side_effect=err):
            with pytest.raises(VideoProcessError) as ei:
                _run_ffmpeg(["ffmpeg"], timeout=30, desc="音频提取")
        # short message to DB; full stderr is logged, not in the exception
        assert "音频提取" in str(ei.value)
        assert "moov atom" not in str(ei.value)


class TestBrowserFriendly:
    """is_browser_friendly decides passthrough vs transcode (asset split)."""

    def _probe(self, fmt, vcodec, acodec):
        import json
        streams = [{"codec_type": "video", "codec_name": vcodec}]
        if acodec is not None:
            streams.append({"codec_type": "audio", "codec_name": acodec})
        return MagicMock(stdout=json.dumps({
            "streams": streams, "format": {"format_name": fmt},
        }))

    def test_h264_aac_mp4_is_friendly(self):
        from app.services import video_processor as vp
        with patch.object(vp, "_run_ffmpeg", return_value=self._probe("mov,mp4,m4a", "h264", "aac")):
            assert vp.is_browser_friendly("x.mp4") is True

    def test_hevc_is_not_friendly(self):
        from app.services import video_processor as vp
        with patch.object(vp, "_run_ffmpeg", return_value=self._probe("mov,mp4", "hevc", "aac")):
            assert vp.is_browser_friendly("x.mp4") is False

    def test_h264_no_audio_is_friendly(self):
        from app.services import video_processor as vp
        with patch.object(vp, "_run_ffmpeg", return_value=self._probe("mp4", "h264", None)):
            assert vp.is_browser_friendly("x.mp4") is True

    def test_probe_failure_defaults_to_transcode(self):
        from app.services import video_processor as vp
        with patch.object(vp, "_run_ffmpeg", side_effect=vp.VideoProcessError("boom")):
            # conservative: probe failure → not friendly → transcode
            assert vp.is_browser_friendly("x.mp4") is False


class TestCaptureFramesResilience:
    """A single frame failing must be skipped, not fatal. The whole-step
    deadline is the only fatal path."""

    def test_single_frame_failure_is_skipped(self, tmp_path):
        from app.services import video_processor as vp

        call_count = {"n": 0}

        def flaky(cmd, timeout, desc):
            # Frame extraction commands contain "-vframes". Fail the 2nd
            # frame only; others succeed.
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise VideoProcessError("simulated bad frame")
            return MagicMock(returncode=0, stdout="", stderr="")

        # duration=90, interval=30 → frames at 0, 30, 60 = 3 attempts,
        # 1 skipped → 2 captured. No exception.
        with patch.object(vp, "_run_ffmpeg", side_effect=flaky):
            frames = vp._capture_frames(
                "fake.mp4", tmp_path, 0, 0, 100, 100,
                interval=30, duration=90,
            )
        assert call_count["n"] == 3
        assert len(frames) == 2  # one skipped, no abort

    def test_step_deadline_is_fatal(self, tmp_path):
        from app.services import video_processor as vp

        # Force the deadline to be already past by making max(300,duration)
        # tiny via monkeypatching time.monotonic to jump forward.
        times = iter([0.0] + [10_000.0] * 50)  # first call sets deadline, rest are "way later"
        with patch.object(vp, "_run_ffmpeg", return_value=MagicMock()), \
             patch.object(vp.time, "monotonic", side_effect=lambda: next(times)):
            with pytest.raises(VideoProcessError) as ei:
                vp._capture_frames(
                    "fake.mp4", tmp_path, 0, 0, 100, 100,
                    interval=30, duration=90,
                )
        assert "超时" in str(ei.value)
