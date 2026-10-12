"""Aliyun nls-filetrans (录音文件识别商用版) service.

Uses Aliyun POP API with HMAC-SHA1 signature V1.
Fully async — no sync IO that blocks the event loop.

API Reliability Layer: _call_api handles transient failures (5xx, 429,
timeout, connection reset) with exponential backoff + jitter, raising
typed exceptions so callers can distinguish retryable vs permanent errors.
"""
import hashlib
import hmac
import base64
import uuid
import time
import asyncio
import json
import random
import aiofiles
from urllib.parse import quote

import httpx

from app.config import get_settings

settings = get_settings()

# ---- constants ----
ENDPOINT = "filetrans.cn-shanghai.aliyuncs.com"
API_VERSION = "2018-08-17"
PRODUCT = "nls-filetrans"

# ---- API Reliability Layer ----

class TransientAPIError(Exception):
    """Retryable — 5xx, 429, timeout, connection reset."""


class PermanentAPIError(Exception):
    """Fatal — 4xx auth / invalid request / quota exceeded."""


def _classify_http_error(status_code: int, body: str) -> Exception:
    """Classify HTTP error into retryable (5xx, 429) or permanent (4xx)."""
    msg = f"HTTP {status_code}"
    if len(body) > 200:
        msg += f", body={body[:200]}"
    if status_code >= 500 or status_code == 429:
        return TransientAPIError(msg)
    return PermanentAPIError(msg)


# ---- HMAC-SHA1 helpers (pure CPU, no IO) ----

def _hmac_sha1(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha1).digest()


def _percent_encode(s: str) -> str:
    unreserved = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~")
    result = []
    for ch in s:
        if ch in unreserved:
            result.append(ch)
        else:
            result.append("%" + format(ord(ch), "02X"))
    return "".join(result)


def _build_signature(method: str, params: dict, access_key_secret: str) -> str:
    sorted_keys = sorted(params.keys())
    canon = "&".join(
        f"{_percent_encode(k)}={_percent_encode(str(params[k]))}" for k in sorted_keys
    )
    string_to_sign = method + "&" + _percent_encode("/") + "&" + _percent_encode(canon)
    key = (access_key_secret + "&").encode("utf-8")
    sig = base64.b64encode(hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()).decode("utf-8")
    return sig


# ---- Async API client (with retry + backoff + jitter) ----

# Retry configuration (tunable per deployment)
API_MAX_RETRIES = 3
API_BASE_DELAY = 1.0         # seconds
API_MAX_DELAY = 30.0         # cap
API_RETRYABLE_CODES = frozenset({429})  # plus all 5xx


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with full jitter."""
    exp = min(API_BASE_DELAY * (2 ** attempt), API_MAX_DELAY)
    return random.uniform(0, exp)


async def _call_api(action: str, method: str = "POST", extra_params: dict | None = None) -> dict:
    """Async Aliyun POP API call with transient failure resilience.

    Retries on 5xx, 429, timeout, and connection errors with
    exponential backoff + jitter. Raises TransientAPIError if all
    retries exhausted, PermanentAPIError on 4xx auth errors.
    """
    ak = settings.aliyun_access_key_id
    sk = settings.aliyun_access_key_secret

    params = {
        "Format": "JSON",
        "Version": API_VERSION,
        "AccessKeyId": ak,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid.uuid4().hex,
        "Action": action,
    }
    if extra_params:
        params.update(extra_params)

    params["Signature"] = _build_signature(method, params, sk)
    url = f"https://{ENDPOINT}/"

    last_error = None
    for attempt in range(API_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                if method == "GET":
                    resp = await client.get(url, params=params)
                else:
                    resp = await client.post(url, data=params)

            if resp.status_code == 200:
                return resp.json()

            # Classify HTTP error
            err = _classify_http_error(resp.status_code, resp.text)
            if isinstance(err, PermanentAPIError):
                raise err  # no retry on 4xx
            # Transient — retry if attempts remain
            last_error = err

        except (TransientAPIError, PermanentAPIError):
            raise  # re-raise immediately (Permanent) or after loop (Transient)
        except (httpx.TimeoutException, httpx.ConnectError,
                httpx.RemoteProtocolError, httpx.ReadError) as e:
            last_error = TransientAPIError(f"API {action} connection error: {e}")

        # Exponential backoff before next retry (skip on last attempt)
        if attempt < API_MAX_RETRIES:
            delay = _backoff_delay(attempt)
            await asyncio.sleep(delay)

    # All retries exhausted
    raise TransientAPIError(
        f"API {action} failed after {API_MAX_RETRIES} retries: {last_error}"
    )


# ---- OSS upload (async) ----

def _oss_sign(method: str, bucket: str, obj: str, headers: dict, resource: str = "") -> str:
    ak = settings.aliyun_access_key_id
    sk = settings.aliyun_access_key_secret
    content_md5 = headers.get("Content-MD5", "")
    content_type = headers.get("Content-Type", "")
    date = headers.get("Date", "")
    canon = f"{method}\n{content_md5}\n{content_type}\n{date}\n"
    oss_headers = sorted(k for k in headers if k.startswith("x-oss-"))
    for h in oss_headers:
        canon += f"{h}:{headers[h]}\n"
    canon += f"/{bucket}/{obj}{resource}"
    sig = base64.b64encode(hmac.new(sk.encode(), canon.encode(), hashlib.sha1).digest()).decode()
    return f"OSS {ak}:{sig}"


async def _upload_to_oss(local_audio_path: str) -> str:
    """Upload WAV to OSS asynchronously, return presigned URL."""
    bucket = settings.aliyun_oss_bucket
    endpoint = settings.aliyun_oss_endpoint
    object_name = f"asr-input/{uuid.uuid4().hex}.wav"

    # Read file asynchronously
    async with aiofiles.open(local_audio_path, "rb") as f:
        data = await f.read()

    content_md5 = base64.b64encode(hashlib.md5(data).digest()).decode()
    gmt = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

    headers = {
        "Date": gmt,
        "Content-Type": "audio/wav",
        "Content-MD5": content_md5,
        "Content-Length": str(len(data)),
    }
    auth = _oss_sign("PUT", bucket, object_name, headers)
    headers["Authorization"] = auth

    put_url = f"https://{bucket}.{endpoint}/{object_name}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.put(put_url, content=data, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"OSS upload failed: {resp.status_code} {resp.text}")

    # Generate presigned GET URL
    expire = int(time.time()) + 7200
    resource = f"/{bucket}/{object_name}"
    string_to_sign = f"GET\n\n\n{expire}\n{resource}"
    sig = base64.b64encode(
        hmac.new(settings.aliyun_access_key_secret.encode(), string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()
    sig_encoded = quote(sig, safe="")
    presigned = (
        f"https://{bucket}.{endpoint}/{object_name}"
        f"?OSSAccessKeyId={settings.aliyun_access_key_id}"
        f"&Expires={expire}"
        f"&Signature={sig_encoded}"
    )
    return presigned


# ---- ASR pipeline (async) ----

async def _submit_task(oss_url: str) -> str:
    task_payload = json.dumps({
        "appkey": settings.aliyun_asr_appkey,
        "file_link": oss_url,
        "enable_words": True,
    })
    result = await _call_api(
        "SubmitTask", method="POST",
        extra_params={"Task": task_payload},
    )
    status = result.get("StatusText", "")
    if status != "SUCCESS":
        raise PermanentAPIError(f"SubmitTask rejected: {result}")
    return result["TaskId"]


async def _poll_result(task_id: str, max_wait: int = 600) -> dict:
    """Poll ASR result. _call_api handles transient HTTP errors internally."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        result = await _call_api("GetTaskResult", method="GET",
                                  extra_params={"TaskId": task_id})
        status = result.get("StatusText", "")
        if status == "SUCCESS":
            raw = result["Result"]
            return json.loads(raw) if isinstance(raw, str) else raw
        if status not in ("RUNNING", "QUEUEING"):
            raise PermanentAPIError(f"GetTaskResult terminal status: {result}")
        await asyncio.sleep(5)
    raise TimeoutError("ASR transcription timed out")


async def transcribe(audio_path: str) -> list[dict]:
    """Transcribe audio file using Aliyun nls-filetrans (商用版).

    Fully async — no blocking IO.
    """
    if not settings.aliyun_access_key_id:
        raise PermanentAPIError("Aliyun credentials not configured")

    oss_url = await _upload_to_oss(audio_path)
    task_id = await _submit_task(oss_url)
    raw_result = await _poll_result(task_id)

    sentences = raw_result.get("Sentences", [])
    segments = []
    for sent in sentences:
        begin_ms = sent.get("BeginTime", 0)
        end_ms = sent.get("EndTime", 0)
        text = sent.get("Text", "").strip()
        if text:
            segments.append({
                "start": round(begin_ms / 1000.0, 1),
                "end": round(end_ms / 1000.0, 1),
                "text": text,
            })
    return segments
