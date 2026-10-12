import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
import sys
sys.path.insert(0, "E:/WorkBuddy.1/2026-05-21-10-10-04/backend")

from faster_whisper import WhisperModel
import time

model = WhisperModel("base", device="cpu", compute_type="int8")
print("Model loaded, transcribing...")
t0 = time.time()

segments, info = model.transcribe(
    "E:/WorkBuddy.1/2026-05-21-10-10-04/audio.wav",
    beam_size=5,
    language="zh",
    vad_filter=True,
    vad_parameters={"min_silence_duration_ms": 500},
)

results = []
total_chars = 0
for seg in segments:
    results.append({
        "start": round(seg.start, 1),
        "end": round(seg.end, 1),
        "text": seg.text.strip(),
    })
    total_chars += len(seg.text.strip())
    elapsed = time.time() - t0
    print(f"[{seg.start:.0f}s] {seg.text[:80]}...")

elapsed = time.time() - t0
print(f"\nDone! {len(results)} segments, {total_chars} chars, {elapsed:.0f}s")

import json
with open("E:/WorkBuddy.1/2026-05-21-10-10-04/transcript.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("Saved to transcript.json")
