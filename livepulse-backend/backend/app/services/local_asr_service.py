"""Local ASR service using faster-whisper.

CPU inference on 16GB Windows laptop.
small model: ~1.5GB RAM.

Audio is chunked into 10-min segments to avoid OOM
on long videos (feature extractor allocates ~1.2GB for 60-min audio).
"""
import os
import time
import logging
import subprocess
import tempfile

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_model = None
FFMPEG = settings.ffmpeg_path
CHUNK_DURATION = 600  # 10 minutes per chunk


def _get_model():
    """Lazy-load faster-whisper model."""
    global _model
    if _model is not None:
        return _model

    path = getattr(settings, "local_whisper_model_path", None) or "f:/models/faster-whisper-small"
    if not os.path.isdir(path):
        raise RuntimeError(f"Local whisper model not found at: {path}")

    os.environ.setdefault("OMP_NUM_THREADS", "4")
    os.environ.setdefault("MKL_NUM_THREADS", "4")

    from faster_whisper import WhisperModel
    logger.info("Loading local whisper model from %s ...", path)
    t0 = time.time()
    _model = WhisperModel(path, device="cpu", compute_type="auto", num_workers=1)
    logger.info("Local whisper model loaded in %.1fs", time.time() - t0)
    return _model


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    ffprobe = settings.ffprobe_path
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
    output = subprocess.check_output(cmd, text=True)
    return float(output.strip())


def _extract_chunk(audio_path: str, start_sec: float, duration_sec: float, chunk_path: str):
    """Extract a chunk of audio using ffmpeg."""
    cmd = [FFMPEG, "-y", "-ss", str(start_sec), "-t", str(duration_sec),
           "-i", audio_path, "-ac", "1", "-ar", "16000", chunk_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return os.path.getsize(chunk_path) > 1000  # not empty


def transcribe_local(audio_path: str) -> list[dict]:
    """Transcribe audio using local faster-whisper small model.

    Splits long audio into 10-min chunks to avoid memory overflow.
    """
    if settings.aliyun_frozen is False and not getattr(settings, "use_local_asr", True):
        from app.services.asr_service import transcribe as aliyun_transcribe
        return aliyun_transcribe(audio_path)

    model = _get_model()
    total_duration = _get_audio_duration(audio_path)
    logger.info("Transcribing %.1f min audio with local whisper...", total_duration / 60)

    t0 = time.time()
    all_segments: list[dict] = []
    offset = 0.0

    while offset < total_duration:
        chunk_dur = min(CHUNK_DURATION, total_duration - offset)
        chunk_path = os.path.join(tempfile.gettempdir(), f"asr_chunk_{int(offset)}.wav")

        logger.info("  Chunk [%.0f:%.0f] (%.0f min)...", offset, offset + chunk_dur, chunk_dur / 60)
        if not _extract_chunk(audio_path, offset, chunk_dur, chunk_path):
            logger.warning("  Empty chunk, skipping")
            offset += chunk_dur
            continue

        segments_raw, _info = model.transcribe(
            chunk_path,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )

        for seg in segments_raw:
            all_segments.append({
                "start": round(offset + seg.start, 2),
                "end": round(offset + seg.end, 2),
                "text": seg.text.strip(),
            })

        # Clean up chunk
        try:
            os.unlink(chunk_path)
        except OSError:
            pass

        offset += chunk_dur

    logger.info("Local transcription finished in %.0fs: %d segments across %d chunks",
                time.time() - t0, len(all_segments),
                int(total_duration / CHUNK_DURATION) + 1)
    return all_segments
