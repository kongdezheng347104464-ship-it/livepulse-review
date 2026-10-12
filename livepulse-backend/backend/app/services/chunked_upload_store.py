"""MinIO-backed store for in-progress chunked uploads.

Chunks live in a separate temp bucket (not local disk) so the upload works
across gunicorn workers — chunk N may hit a different process than chunk N+1.

Layout per upload:
    {upload_id}/meta.json          init metadata (anchor binding, title, owner)
    {upload_id}/chunk_000000       chunk bytes (zero-padded → lexical == numeric order)
    {upload_id}/chunk_000001
    ...
"""
from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from minio import Minio


def meta_key(upload_id: str) -> str:
    return f"{upload_id}/meta.json"


def chunk_key(upload_id: str, index: int) -> str:
    return f"{upload_id}/chunk_{index:06d}"


def ensure_bucket(client: Minio, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def write_meta(client: Minio, bucket: str, upload_id: str, meta: dict) -> None:
    raw = json.dumps(meta).encode("utf-8")
    client.put_object(
        bucket, meta_key(upload_id), io.BytesIO(raw), length=len(raw),
        content_type="application/json",
    )


def read_meta(client: Minio, bucket: str, upload_id: str) -> dict | None:
    """Returns the upload's meta dict, or None if the upload_id is unknown."""
    try:
        resp = client.get_object(bucket, meta_key(upload_id))
    except Exception:
        return None
    try:
        return json.loads(resp.read())
    finally:
        resp.close()
        resp.release_conn()


def put_chunk(client: Minio, bucket: str, upload_id: str, index: int, data: bytes) -> None:
    client.put_object(
        bucket, chunk_key(upload_id, index), io.BytesIO(data), length=len(data),
        content_type="application/octet-stream",
    )


def received_indices(client: Minio, bucket: str, upload_id: str) -> set[int]:
    """Set of chunk indices already stored for this upload."""
    out: set[int] = set()
    prefix = f"{upload_id}/chunk_"
    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        name = obj.object_name or ""
        tail = name.rsplit("chunk_", 1)[-1]
        if tail.isdigit():
            out.add(int(tail))
    return out


def missing_chunks(received: set[int], total: int) -> list[int]:
    """Pure: which of 0..total-1 are not yet received (sorted)."""
    return sorted(set(range(total)) - received)


def assemble_to_main(
    client: Minio,
    temp_bucket: str,
    upload_id: str,
    total_chunks: int,
    main_bucket: str,
    object_name: str,
    content_type: str = "video/mp4",
) -> None:
    """Download chunks 0..total-1 in order, concat to a local temp file,
    then fput the assembled file into the main bucket. Streams each chunk
    (never holds the whole video in memory).
    """
    tmp_path = Path(tempfile.gettempdir()) / f"assemble_{upload_id}_{object_name}"
    try:
        with open(tmp_path, "wb") as out:
            for i in range(total_chunks):
                resp = client.get_object(temp_bucket, chunk_key(upload_id, i))
                try:
                    for block in resp.stream(8 * 1024 * 1024):
                        out.write(block)
                finally:
                    resp.close()
                    resp.release_conn()
        client.fput_object(main_bucket, object_name, str(tmp_path), content_type=content_type)
    finally:
        tmp_path.unlink(missing_ok=True)


def cleanup(client: Minio, bucket: str, upload_id: str) -> None:
    """Remove all objects for an upload (chunks + meta). Best-effort."""
    for obj in client.list_objects(bucket, prefix=f"{upload_id}/", recursive=True):
        try:
            client.remove_object(bucket, obj.object_name)
        except Exception:
            pass
