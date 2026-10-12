import math
import uuid
import tempfile
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from minio import Minio
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.services.auth.current_user import UserContext, get_admin_or_user
from app.services.ownership import assert_owned_anchor
from app.services.upload_finalize import finalize_video_upload, validate_anchor_binding
from app.services import chunked_upload_store as store

router = APIRouter(prefix="/upload", tags=["upload"])
settings = get_settings()

ALLOWED_MIME = {
    "video/mp4", "video/x-matroska", "video/quicktime",
    "video/webm", "video/x-msvideo",
}
ALLOWED_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB


def _minio_client():
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


@router.post("/video")
async def upload_video(
    file: UploadFile = File(...),
    anchor_id: str | None = Form(default=None),
    new_anchor_name: str | None = Form(default=None),
    title: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Upload a video for analysis.

    Anchor binding is now **explicit**: exactly one of `anchor_id` (attach
    to existing) or `new_anchor_name` (create new) must be provided. The
    previous "anchor_name → get-or-create" path silently forked anchors
    on every typo, producing 6 fragmented identities from intended-3.

    Returns 400 if ambiguous (both / neither given) or if `new_anchor_name`
    canonicalizes to an existing anchor (caller should use that one).
    """
    # ---- anchor binding (must be exactly one of attach / create) ----
    validate_anchor_binding(anchor_id, new_anchor_name)

    # QA 2026-05-30 M0 / U-12: previously hard-rejected when content_type
    # wasn't in `allowed`. But:
    #   - curl multipart defaults to application/octet-stream
    #   - some browsers fall back to octet-stream for uppercase extensions
    #     (.MP4, .MOV) when the OS MIME table lookup fails
    #   - iPhone screen recordings often hit this path
    # Real users would have been silently blocked. Fix: when content_type
    # is missing OR generic (octet-stream), fall back to the file
    # extension. Hard reject only when content_type is set AND clearly
    # wrong (e.g. image/png).
    suffix = Path(file.filename or "").suffix.lower() or ".mp4"
    ct = file.content_type
    is_generic_ct = ct in (None, "", "application/octet-stream")
    if is_generic_ct:
        if suffix not in ALLOWED_EXT:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported format: extension {suffix} (content-type missing)",
            )
    elif ct not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail=f"unsupported format: {ct}")

    object_name = f"{uuid.uuid4().hex}{suffix}"

    tmp_path = Path(tempfile.gettempdir()) / object_name
    try:
        total_bytes = 0
        with open(tmp_path, "wb") as f:
            while True:
                chunk = await file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="file too large (max 2 GB)")
                f.write(chunk)
    except HTTPException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        raise HTTPException(status_code=500, detail=f"failed to save uploaded file: {e}")

    try:
        client = _minio_client()
        client.fput_object(
            settings.minio_bucket, object_name, str(tmp_path),
            content_type=file.content_type or "video/mp4",
        )
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"minio upload failed: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)

    # Object is in the main bucket — resolve anchor / create session / trigger.
    data = await finalize_video_upload(
        object_name=object_name,
        anchor_id=anchor_id,
        new_anchor_name=new_anchor_name,
        title=title,
        filename=file.filename,
        ctx=ctx,
        db=db,
    )
    return {"code": 0, "data": data, "message": "ok"}


# ===========================================================================
# Chunked upload (Sprint 3, for iOS): init → chunk* → complete, + status.
# Chunks land in MinIO temp bucket (multi-worker safe). See
# app/services/chunked_upload_store.py + docs/04-backend-changes.md §2.
# ===========================================================================


class ChunkedInitRequest(BaseModel):
    filename: str
    total_size: int
    anchor_id: str | None = None
    new_anchor_name: str | None = None
    title: str = ""


def _suffix_for(filename: str) -> str:
    """Validate extension, return normalized suffix. 400 if unsupported."""
    suffix = Path(filename or "").suffix.lower() or ".mp4"
    if suffix not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail=f"unsupported format: {suffix}")
    return suffix


def _load_owned_meta(upload_id: str, ctx: UserContext) -> dict:
    """Read meta + enforce owner-only access (admin bypass). 404 if unknown
    or not owned — 404-not-403 keeps upload_ids non-enumerable.
    """
    client = _minio_client()
    meta = store.read_meta(client, settings.minio_temp_bucket, upload_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="upload not found")
    if meta.get("owner_user_id") != ctx.id and ctx.role != "admin":
        raise HTTPException(status_code=404, detail="upload not found")
    return meta


@router.post("/init")
async def chunked_init(
    body: ChunkedInitRequest,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Start a chunked upload. Validates format + size + anchor binding up
    front (fail fast before any bytes), returns an upload_id + chunk size.
    """
    _suffix_for(body.filename)
    if body.total_size <= 0 or body.total_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="invalid total_size (max 2 GB)")
    validate_anchor_binding(body.anchor_id, body.new_anchor_name)
    # If attaching to an existing anchor, validate ownership now (fail fast).
    if body.anchor_id:
        await assert_owned_anchor(body.anchor_id, ctx, db)

    upload_id = uuid.uuid4().hex
    chunk_size = settings.chunk_size_bytes
    total_chunks = math.ceil(body.total_size / chunk_size)

    client = _minio_client()
    store.ensure_bucket(client, settings.minio_temp_bucket)
    store.write_meta(client, settings.minio_temp_bucket, upload_id, {
        "upload_id": upload_id,
        "filename": body.filename,
        "total_size": body.total_size,
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
        "anchor_id": body.anchor_id,
        "new_anchor_name": body.new_anchor_name,
        "title": body.title,
        "owner_user_id": ctx.id,
    })

    return {
        "code": 0,
        "data": {"upload_id": upload_id, "chunk_size": chunk_size, "total_chunks": total_chunks},
        "message": "ok",
    }


@router.post("/chunk/{upload_id}")
async def chunked_put(
    upload_id: str,
    chunk_index: int = Form(...),
    file: UploadFile = File(...),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Upload one chunk. Idempotent per index (re-uploading overwrites)."""
    meta = _load_owned_meta(upload_id, ctx)
    total_chunks = meta["total_chunks"]
    if chunk_index < 0 or chunk_index >= total_chunks:
        raise HTTPException(status_code=400, detail=f"chunk_index out of range [0,{total_chunks})")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty chunk")

    client = _minio_client()
    store.put_chunk(client, settings.minio_temp_bucket, upload_id, chunk_index, data)
    received = sorted(store.received_indices(client, settings.minio_temp_bucket, upload_id))
    return {"code": 0, "data": {"received_chunks": received}, "message": "ok"}


@router.get("/status/{upload_id}")
async def chunked_status(
    upload_id: str,
    ctx: UserContext = Depends(get_admin_or_user),
):
    meta = _load_owned_meta(upload_id, ctx)
    client = _minio_client()
    received = sorted(store.received_indices(client, settings.minio_temp_bucket, upload_id))
    return {
        "code": 0,
        "data": {"received_chunks": received, "total_chunks": meta["total_chunks"]},
        "message": "ok",
    }


@router.post("/complete/{upload_id}")
async def chunked_complete(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Assemble chunks → main bucket → create session → trigger analysis."""
    meta = _load_owned_meta(upload_id, ctx)
    total_chunks = meta["total_chunks"]

    client = _minio_client()
    received = store.received_indices(client, settings.minio_temp_bucket, upload_id)
    missing = store.missing_chunks(received, total_chunks)
    if missing:
        raise HTTPException(status_code=400, detail={"error": "missing chunks", "missing": missing})

    suffix = _suffix_for(meta["filename"])
    object_name = f"{uuid.uuid4().hex}{suffix}"
    try:
        store.assemble_to_main(
            client, settings.minio_temp_bucket, upload_id, total_chunks,
            settings.minio_bucket, object_name,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"assemble failed: {e}")

    data = await finalize_video_upload(
        object_name=object_name,
        anchor_id=meta.get("anchor_id"),
        new_anchor_name=meta.get("new_anchor_name"),
        title=meta.get("title", ""),
        filename=meta.get("filename"),
        ctx=ctx,
        db=db,
    )

    # Best-effort cleanup of temp chunks once the session exists.
    store.cleanup(client, settings.minio_temp_bucket, upload_id)

    return {"code": 0, "data": data, "message": "ok"}
