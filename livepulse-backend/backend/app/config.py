import os
import platform
import shutil
from pydantic_settings import BaseSettings
from functools import lru_cache


# Windows default install path for ffmpeg via WinGet — fallback only when
# both env var and PATH lookup fail. Linux/Docker should rely on PATH (apt-get
# installed ffmpeg lands in /usr/bin).
_WINDOWS_FFMPEG_FALLBACK = (
    r"C:\Users\<user>\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
)
_WINDOWS_FFPROBE_FALLBACK = (
    r"C:\Users\<user>\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.1.1-full_build\bin\ffprobe.exe"
)


def _resolve_binary(name: str, windows_fallback: str) -> str:
    """Locate an ffmpeg-family binary across platforms.

    Resolution order:
      1. PATH lookup (Linux/Docker/macOS standard install — works in container)
      2. Windows WinGet fallback (developer machine default)
      3. Bare name (let subprocess fail with a clear message at first use)
    """
    found = shutil.which(name)
    if found:
        return found
    if platform.system() == "Windows" and os.path.exists(windows_fallback):
        return windows_fallback
    return name


class Settings(BaseSettings):
    # === Secrets (no defaults — fail fast on startup if missing) ===
    database_url: str
    minio_access_key: str
    minio_secret_key: str

    # === Optional secrets (empty = feature disabled) ===
    # 阿里云凭证为空时 OCR/ASR 不可用(各调用点已有 `if not ...` 守卫);
    # 配合 DEMO_MODE=1 可在没有任何云 key 的情况下浏览合成样例。
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""
    # DEMO_MODE=1: 启动时写入合成样例数据(见 services/demo_seed.py),不调用任何云 AI。
    demo_mode: bool = False
    aliyun_asr_appkey: str = ""
    aliyun_oss_bucket: str = ""
    api_key: str = ""
    feishu_webhook_url: str = ""
    # Empty → AI range-analysis endpoint returns 503 with clear message.
    # Set in backend/.env: ANTHROPIC_API_KEY=sk-ant-...
    anthropic_api_key: str = ""
    # Model name passed to anthropic SDK. Defaulting to claude-sonnet-4-5
    # (current strongest non-Opus model, good quality/cost balance for
    # 1-2 page analysis prompts). Override via env if needed.
    anthropic_model: str = "claude-sonnet-4-5"

    # QA 2026-05-30 G: LLM provider switch. AI Range Analysis 后端可以
    # 在 claude / qwen 之间切换。
    #   - claude  → AsyncAnthropic (国外服务,国内偶尔需 proxy)
    #   - qwen    → DashScope OpenAI-compatible endpoint (国内骨干)
    # 当前默认 qwen — Anthropic 账户余额耗尽时的过渡方案。
    llm_provider: str = "qwen"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # qwen-max (stable alias), NOT qwen-max-latest: the -latest rolling
    # snapshot requires separate allowlist access on 百炼/DashScope and
    # returns 403 access_denied for accounts without it (2026-06-03). The
    # stable alias is the same flagship tier, generally available.
    qwen_model: str = "qwen-max"

    # === Infrastructure (safe defaults) ===
    redis_url: str = "redis://localhost:6379/0"
    minio_endpoint: str = "localhost:9002"
    # presigned 视频/高光 URL 对外主机(客户端可达,如局域网 IP:9002 或公网域名)。
    # 空 = 回退用 minio_endpoint。后端自己读写 MinIO 仍走内部 minio_endpoint。
    minio_public_endpoint: str = ""
    minio_bucket: str = "livestream-videos"
    minio_secure: bool = False

    # === Chunked upload (Sprint 3, for iOS) ===
    # In-progress chunks land in a separate bucket so a lifecycle/TTL policy
    # can later sweep abandoned uploads without touching analysed videos.
    minio_temp_bucket: str = "uploads-temp"
    # Recommended chunk size handed back to the client at /upload/init.
    chunk_size_bytes: int = 10 * 1024 * 1024  # 10 MB

    # === A6 Celery concurrency (2026-06-03) ===
    # When True, upload / reanalyze triggers go via Celery .delay() and the
    # work runs on a separate worker process (per-user backpressure handled
    # by Celery + Redis queue). When False, falls back to the original
    # asyncio.create_task path with an in-process Semaphore(1) — useful for
    # dev where booting Redis + worker is friction.
    #
    # Default False so that fresh checkouts still work without redis. Flip
    # to True in production .env (and ensure `make redis-up && make worker-bg`).
    use_celery: bool = False

    # === AliCloud (non-secret config) ===
    aliyun_oss_endpoint: str = "oss-cn-shanghai.aliyuncs.com"
    aliyun_asr_region: str = "cn-shanghai"
    aliyun_frozen: bool = True

    # === Local ASR ===
    whisper_model_size: str = "small"
    # Empty default → falls back to a container-friendly path; the actual model
    # is mounted via volume on Linux/Docker.
    local_whisper_model_path: str = ""
    use_local_asr: bool = True

    # === Paths === (resolved from PATH at construct time; override via env)
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    danmaku_region: str = "100,750,1720,300"

    # === App ===
    app_name: str = "LivePulse"
    debug: bool = False
    # === Observability ===
    # Sentry DSN — empty means Sentry init is a no-op (dev environments).
    # Get one at https://sentry.io → create project → copy DSN here.
    sentry_dsn: str = ""
    # Environment label sent to Sentry (dev/staging/prod) so errors can be
    # filtered. Defaults to "dev"; override in .env to "prod" on production.
    app_env: str = "dev"

    # === Auth (A1 2026-05-31) ===
    # Hex-encoded random secret (>= 32 chars). Generate with
    # `openssl rand -hex 32`. Empty/short = backend refuses to issue JWTs.
    jwt_secret: str = ""
    # Seed admin user — created on startup if absent. Leave blank to skip
    # seeding (e.g., when restoring from a snapshot that already has admin).
    admin_email: str = ""
    admin_password: str = ""
    # X-API-Key bypass: when True, requests with valid X-API-Key are
    # promoted to admin scope without needing a JWT. Set to false on
    # production hardening (A3) to remove the backdoor entirely.
    allow_admin_api_key: bool = True
    # User cache TTL for the auth middleware. Trade-off: role / is_active
    # changes take up to N seconds to propagate to live requests. Set to
    # 0 in dev to always hit DB.
    user_cache_ttl_sec: int = 5
    # slowapi rate limit string for /auth/register + /auth/login (per IP).
    # Production = "5/minute"; dev / tests set higher to avoid 429 from
    # the test runner hitting the live backend in tight loops.
    auth_rate_limit: str = "100/minute"
    # Comma-separated list of allowed CORS origins. Override per-env via
    # CORS_ALLOW_ORIGINS=https://app.example.com,https://admin.example.com
    cors_allow_origins: str = (
        "http://localhost:3001,http://localhost:3000,"
        "http://127.0.0.1:3001,http://127.0.0.1:3000"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def model_post_init(self, __context) -> None:
        # Fill ffmpeg / ffprobe / model path lazily so env vars win, then PATH,
        # then Windows-specific fallback, then bare name.
        if not self.ffmpeg_path:
            self.ffmpeg_path = _resolve_binary("ffmpeg", _WINDOWS_FFMPEG_FALLBACK)
        if not self.ffprobe_path:
            self.ffprobe_path = _resolve_binary("ffprobe", _WINDOWS_FFPROBE_FALLBACK)
        if not self.local_whisper_model_path:
            # Container convention: mount the model at /models/...
            # Developer convention: f:\models\... on the Windows host.
            if platform.system() == "Windows":
                self.local_whisper_model_path = r"f:\models\faster-whisper-small"
            else:
                self.local_whisper_model_path = "/models/faster-whisper-small"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
