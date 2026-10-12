"""Minimal ASR test with 10s audio — no audio extraction, just upload + API."""
import os, sys, json, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# 从环境变量读取阿里云凭证(见 .env.example),不要硬编码
for _k in ("ALIYUN_ACCESS_KEY_ID","ALIYUN_ACCESS_KEY_SECRET","ALIYUN_ASR_APPKEY","ALIYUN_OSS_BUCKET"):
    if not os.environ.get(_k):
        sys.exit(f"缺少环境变量 {_k},请先配置 .env")


from app.services.asr_service import _upload_to_oss, transcribe

audio = r"E:\test_10s.wav"

print("1. Uploading 10s audio to OSS...", flush=True)
try:
    url = _upload_to_oss(audio)
    print(f"   OSS URL: {url[:80]}...", flush=True)
except Exception as e:
    print(f"   UPLOAD FAILED: {e}", flush=True)
    sys.exit(1)

print("2. Submitting ASR task...", flush=True)
try:
    result = transcribe(audio)  # This does upload + submit + poll
    print(f"   ASR result: {json.dumps(result, ensure_ascii=False, indent=2)[:2000]}", flush=True)
    print("\n=== ✅ ASR SUCCESS ===", flush=True)
except Exception as e:
    print(f"   ASR FAILED: {type(e).__name__}: {e}", flush=True)
    print("\n=== ❌ ASR FAILED ===", flush=True)
