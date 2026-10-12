"""Video → (audio + region frames) extraction.

Two output channels for OCR consumption:
  viewer_frame_paths       — crops of the top-right "在线观众·N" badge
  interaction_frame_paths  — crops of the right-side interaction list

The legacy `frame_paths` attribute is kept for backward compatibility:
it points to the interaction_frame_paths (because that's the bigger,
more informative region — anywhere downstream code did "loop over frames"
should still work). The floating-danmaku region overlay is no longer
cropped — it was the source of the gift/system pollution that
necessitated this whole event-stream redesign (see 2026-05-28 work).
"""
from pathlib import Path
import logging
import os
import tempfile
import subprocess
import time
from app.config import get_settings
from app.services.live_event.roi_config import (
    VIEWER_REGION, INTERACTION_REGION, parse_resolution,
)

_settings = get_settings()
FFMPEG = _settings.ffmpeg_path
FFPROBE = _settings.ffprobe_path

_logger = logging.getLogger(__name__)


class VideoProcessError(RuntimeError):
    """Raised when an ffmpeg/ffprobe subprocess times out or fails.

    Carries a short message suitable for the DB error_message column; the
    full stderr is logged separately (see _run_ffmpeg) so ops can debug
    without bloating the user-facing error.
    """


def _run_ffmpeg(cmd: list, timeout: float, desc: str) -> subprocess.CompletedProcess:
    """Run an ffmpeg/ffprobe command with a hard timeout.

    Audit 2026-06-03 P1-1: without a timeout, a corrupt frame or stuck
    encode hangs the subprocess forever → freezes the celery worker → the
    concurrency pool degrades until everything stalls. A bounded timeout
    converts "hang forever" into "clean error the pipeline can handle".

    On failure the full stderr tail goes to the log (not the exception)
    so the DB error_message stays short. We use message interpolation,
    NOT logging `extra=`, because the project's plain-text formatter
    silently drops extra fields.
    """
    try:
        return subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _logger.error("[ffmpeg] %s TIMED OUT after %.0fs — cmd=%s",
                      desc, timeout, " ".join(cmd))
        raise VideoProcessError(f"{desc} 超时（>{timeout:.0f}s），疑似卡死或损坏帧")
    except subprocess.CalledProcessError as e:
        stderr_tail = (e.stderr or "")[-4000:]
        _logger.error("[ffmpeg] %s failed (stderr tail):\n%s", desc, stderr_tail)
        raise VideoProcessError(f"{desc} 失败")


class VideoProcessResult:
    def __init__(
        self,
        audio_path: Path,
        viewer_frame_paths: list[Path],
        full_frame_paths: list[Path],
        duration_sec: float,
        resolution: str,
    ):
        self.audio_path = audio_path
        # Two output channels:
        #   viewer_frame_paths — small ROI crop of the "在线观众·N" badge.
        #     Tight crop because the badge is a high-precision target
        #     and a small crop avoids the OCR engine accidentally
        #     latching onto other numbers elsewhere in the frame.
        #   full_frame_paths — uncropped full frames.
        #     Used by the interaction-list extractor: the source_classifier
        #     finds username:content lines from anywhere in the picture,
        #     so cropping a specific region would only have downside
        #     (UI tab dependence, recording window size dependence, etc).
        # See 2026-05-28 design decision in roi_config.py.
        self.viewer_frame_paths = viewer_frame_paths
        self.full_frame_paths = full_frame_paths
        self.duration_sec = duration_sec
        # Recorded as "WIDTHxHEIGHT". Stored back on the session so future
        # reruns can use the SAME relative→pixel conversion (UI consistency
        # is recording-specific; mixing resolutions across reruns would
        # silently shift the crop regions).
        self.resolution = resolution

    # ----- Backward compatibility aliases -----
    # Pre-refactor pipeline code referenced ctx.proc.frame_paths /
    # ctx.proc.interaction_frame_paths. Both now point at full_frame_paths
    # (the full-frame channel that the interaction OCR consumes), so
    # downstream code keeps working without name changes.
    @property
    def frame_paths(self) -> list[Path]:
        return self.full_frame_paths

    @property
    def interaction_frame_paths(self) -> list[Path]:
        # Soft alias. The interaction OCR went from "cropped ROI" to
        # "full frame" on 2026-05-28; the field name is preserved so
        # existing call sites work without changes. ROI cropping code
        # is still in this file (capture_frames is unchanged) — kept
        # for a possible future re-introduction; see roi_config.py.
        return self.full_frame_paths


def process_video(
    video_path: str,
    output_dir: Path | None = None,
    # Kept for signature compatibility with the previous pipeline code,
    # but no longer used — danmaku_region cropping was removed because
    # the floating-danmaku layer was the noise source. Future cleanup
    # should remove this parameter; left in place to avoid breaking
    # the _step_video call site in this same migration.
    danmaku_region: str = "",  # deprecated, ignored
    frame_interval: int = 30,
) -> VideoProcessResult:
    """Extract audio + capture viewer-badge and interaction-list region frames.

    Args:
        video_path: Local path to the source video file.
        output_dir: Where to write outputs. Temp dir if None.
        danmaku_region: DEPRECATED. Ignored. Kept for call-site compat.
        frame_interval: Seconds between successive captures (default 30s,
            matching the existing OCR cadence — same temporal granularity
            as the pre-2026-05-28 setup).

    Returns:
        VideoProcessResult with audio + the two structured frame channels.
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="livestream_proc_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    if not os.path.exists(FFMPEG):
        raise RuntimeError(f"ffmpeg not found at {FFMPEG}")
    if not os.path.exists(FFPROBE):
        raise RuntimeError(f"ffprobe not found at {FFPROBE}")

    # ---- Metadata + ROI calibration (probe FIRST: audit 2026-06-03) ----
    # Moved ahead of audio extraction so duration_sec is available to size
    # the audio extraction timeout. probe has no dependency on audio; the
    # old order was historical accident.
    duration_sec, resolution = _probe_metadata(video_path)
    width, height = parse_resolution(resolution)

    # ---- Audio ----
    # Timeout scales with video length (audio extraction is well under
    # realtime, so duration*2 is very generous headroom).
    audio_path = output_dir / "audio.wav"
    _extract_audio(video_path, str(audio_path), timeout=max(300.0, duration_sec * 2))

    # Translate the two relative ROIs to absolute pixel rects for THIS
    # source resolution. parse_resolution gives us a safe default
    # (1920x1080) if metadata is incomplete; to_pixels raises clearly
    # if the resulting rect would be too small to OCR.
    viewer_x, viewer_y, viewer_w, viewer_h = VIEWER_REGION.to_pixels(width, height)
    inter_x, inter_y, inter_w, inter_h = INTERACTION_REGION.to_pixels(width, height)

    # Per-ROI subdirectories so debugging a frame doesn't require
    # disambiguating which crop produced which file — and so rsync /
    # mc cp to S3 can stream them in separate streams without
    # interleaving.
    viewer_dir = output_dir / "viewer"
    full_dir = output_dir / "full"
    viewer_dir.mkdir(exist_ok=True)
    full_dir.mkdir(exist_ok=True)

    # Small ROI crop for the viewer-count badge.
    viewer_frame_paths = _capture_frames(
        video_path, viewer_dir,
        viewer_x, viewer_y, viewer_w, viewer_h,
        interval=frame_interval, duration=duration_sec,
    )
    # Full uncropped frames for the interaction extractor. We pass the
    # natural frame dims as the "crop" rect, which is a no-op but lets
    # _capture_frames stay as the single capture helper — every
    # downstream tool already understands that interface.
    full_frame_paths = _capture_frames(
        video_path, full_dir,
        0, 0, width, height,
        interval=frame_interval, duration=duration_sec,
    )

    return VideoProcessResult(
        audio_path=audio_path,
        viewer_frame_paths=viewer_frame_paths,
        full_frame_paths=full_frame_paths,
        duration_sec=duration_sec,
        resolution=resolution,
    )


def _extract_audio(video_path: str, output_path: str, timeout: float = 1800.0) -> None:
    cmd = [
        FFMPEG, "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_path,
    ]
    _run_ffmpeg(cmd, timeout=timeout, desc="音频提取")


def remux_with_faststart(input_path: str, output_path: str, timeout: float = 300.0) -> None:
    """Remux an H.264 MP4 moving the moov atom to the file start (faststart).

    Stream-copy only — no re-encode, no quality loss. Typical time: ~30s
    for a 1h recording (vs ~5min for a full transcode).

    OBS writes moov at the END of the file (duration unknown during recording).
    Without faststart, the browser must fetch the file tail on every seek →
    multi-second stall per frame. With faststart the moov is at byte 0:
    instant play + fast seek via HTTP Range requests.
    """
    cmd = [
        FFMPEG, "-y",
        "-i", input_path,
        "-c:v", "copy",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    _run_ffmpeg(cmd, timeout=timeout, desc="MP4 faststart remux")


def transcode_for_playback(input_path: str, output_path: str, timeout: float = 3600.0) -> None:
    """Re-encode source video into a browser-friendly playback format.

    QA 2026-05-30: original sources from phone screen recordings are
    typically HEVC (h265) + 60-120fps + 1080p portrait + 10-20 Mbps.
    Mac Intel + browser <video> can't hardware-decode HEVC → CPU software
    decode → ~40 seconds per frame in Workspace replay (effectively
    unplayable). OBS desktop recordings hit similar issues with high
    bitrates and non-faststart containers.

    Output target: H.264 main profile + 720p (long side) + 30fps +
    yuv420p + faststart + AAC 128k. ~5x size reduction, universal
    browser hardware decode support.

    Encoding speed: ~2x realtime on Mac Intel veryfast preset (11min
    source ≈ 5 min encode). Runs at pipeline end so user never sees the
    raw file (Workspace entry is after analysis completes anyway).
    """
    cmd = [
        FFMPEG, "-y",
        "-i", input_path,
        # Video: H.264 main + faststart + yuv420p (universal compatibility)
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-profile:v", "main",
        "-pix_fmt", "yuv420p",
        # Cap framerate at 30fps + downscale long side to 720 (keep aspect)
        "-vf",
        "fps=30,scale='if(gt(iw,ih),min(1280,iw),trunc(min(720,iw)/2)*2)':'if(gt(iw,ih),trunc(min(720,ih)/2)*2,min(1280,ih))'",
        # Audio: AAC 128k (universal)
        "-c:a", "aac", "-b:a", "128k",
        # faststart = move moov atom to file start → browser can begin
        # playback before fully downloading
        "-movflags", "+faststart",
        output_path,
    ]
    _run_ffmpeg(cmd, timeout=timeout, desc="视频转码")


def _probe_metadata(video_path: str) -> tuple[float, str]:
    cmd = [
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        video_path,
    ]
    # Metadata read should be near-instant; 60s = definitely stuck.
    result = _run_ffmpeg(cmd, timeout=60.0, desc="元数据探测")
    lines = result.stdout.strip().split("\n")
    duration = 0.0
    resolution = "unknown"
    for line in lines:
        line = line.strip()
        if line and "," in line and line.split(",")[0].isdigit():
            parts = line.split(",")
            if len(parts) == 2:
                resolution = f"{parts[0]}x{parts[1]}"
        elif line and line.replace(".", "").isdigit():
            duration = float(line)
    return duration, resolution


def is_browser_friendly(video_path: str) -> bool:
    """True if the source can be served to a browser <video> as-is.

    Browser-friendly = H.264 video + AAC (or no) audio + mp4/mov container.
    Such sources (most desktop OBS recordings) need NO transcode — we point
    playback_video_path straight at the master, saving both the encode time
    AND a second copy in storage. Phone HEVC/120fps sources fail this and
    get transcoded to a separate playback file (master preserved).

    Conservative: any probe failure or unexpected codec → False (transcode),
    because a false "friendly" means unplayable video in the browser, while
    a false "not friendly" just costs an unnecessary transcode.
    """
    cmd = [
        FFPROBE, "-v", "error",
        "-show_entries", "stream=codec_type,codec_name",
        "-show_entries", "format=format_name",
        "-of", "json",
        video_path,
    ]
    try:
        result = _run_ffmpeg(cmd, timeout=60.0, desc="编码探测")
        import json
        data = json.loads(result.stdout)
    except (VideoProcessError, ValueError) as e:
        _logger.warning("[browser_friendly] probe failed, will transcode: %s", e)
        return False

    streams = data.get("streams", [])
    fmt = (data.get("format", {}).get("format_name") or "").lower()
    vcodec = next((s.get("codec_name") for s in streams if s.get("codec_type") == "video"), None)
    acodec = next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), None)

    container_ok = any(c in fmt for c in ("mp4", "mov", "m4a", "3gp"))
    video_ok = vcodec in ("h264", "avc1")
    audio_ok = acodec in ("aac", "mp4a", None)  # None = no audio track, fine
    friendly = container_ok and video_ok and audio_ok
    _logger.info(
        "[browser_friendly] %s → %s (container=%s vcodec=%s acodec=%s)",
        video_path, friendly, fmt, vcodec, acodec,
    )
    return friendly


def _capture_frames(
    video_path: str,
    output_dir: Path,
    x: int, y: int, w: int, h: int,
    interval: int = 30,
    duration: float | None = None,
) -> list[Path]:
    """Sample frames from video at fixed interval, cropping to (x, y, w, h).

    Optional `duration` parameter: callers that already probed it can
    pass it in to avoid a redundant ffprobe call. None → re-probes
    (matches legacy behavior).
    """
    if duration is None:
        duration, _ = _probe_metadata(video_path)

    # Audit 2026-06-03 P1-1: two-tier timeout.
    #   - step_deadline bounds the WHOLE extraction loop, so N small
    #     per-frame timeouts can't accumulate into hours.
    #   - per-frame cap (min(remaining, 30)) trips a single stuck frame
    #     within 30s without waiting out the whole deadline.
    # A single frame that fails/times out is SKIPPED, not fatal — one
    # corrupt frame at minute 45 must not kill a 90-min analysis. A
    # missing frame is an explicit gap downstream (same philosophy as
    # viewer_count/like_count extraction). Only the step deadline is fatal.
    step_deadline = time.monotonic() + max(300.0, duration)
    frame_paths = []
    second = 0
    skipped = 0
    while second < duration:
        remaining = step_deadline - time.monotonic()
        if remaining <= 0:
            raise VideoProcessError(
                f"抽帧整体超时（>{max(300.0, duration):.0f}s），已抽 {len(frame_paths)} 帧"
            )
        frame_path = output_dir / f"frame_{second:06d}.png"
        cmd = [
            FFMPEG, "-y",
            "-ss", str(second),
            "-i", video_path,
            "-vframes", "1",
            "-filter:v", f"crop={w}:{h}:{x}:{y}",
            "-q:v", "2",
            str(frame_path),
        ]
        try:
            _run_ffmpeg(cmd, timeout=min(remaining, 30.0), desc=f"抽帧@{second}s")
            frame_paths.append(frame_path)
        except VideoProcessError as e:
            # Skip this frame, keep going. Gap = explicit absence downstream.
            skipped += 1
            _logger.warning("[capture_frames] skip frame @%ds: %s", second, e)
        second += interval
    if skipped:
        _logger.info("[capture_frames] done: %d captured, %d skipped",
                     len(frame_paths), skipped)
    return frame_paths
