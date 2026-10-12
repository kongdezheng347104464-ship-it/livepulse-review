"""Runtime fingerprint — single source of truth about which process is
actually serving the current request.

Why this exists: on 2026-05-27 we discovered that the dev box was running
multiple uvicorn workers simultaneously — some via the project venv,
some via system Python, several of them holding old `pipeline.py` byte
code from before the F-2 composite_score redesign. Requests landed on
whichever worker won the kernel's port-accept race, producing data that
didn't match the source on disk. There was no way to ask "who exactly
just served that request?" from the outside.

This module answers that question. It computes a fingerprint ONCE per
process at import time, then exposes it via two channels that MUST stay
in sync:

  1. `boot_log_line()` — emitted at startup so historical logs can be
     grepped by boot_id / pipeline_hash to identify which worker handled
     a given request, even after that worker is long dead.
  2. `get_fingerprint()` — read by the `/api/v1/_debug/runtime` endpoint
     so restart scripts can poll until boot_id changes (proving the new
     code is actually serving traffic, not just "uvicorn says ready").

Both surfaces read from the same cached dict. Drift between them would
itself be a bug — they MUST be identical or the gatekeeper falls apart.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Computed once at module import. Holds the immutable identity of THIS
# process. Re-reading is intentionally cheap (dict lookup); we never want
# the endpoint or the log line to disagree about what the process is.
_FINGERPRINT: dict | None = None


def _compute_pipeline_hash() -> str:
    """SHA-1 of every .py file under backend/app/.

    Single-file hashing (just pipeline.py) would miss changes in
    segment_scorer, tactics, insight_engine, etc. — all of which feed
    into the composite_score that today's bug was about. Hashing the
    whole package guarantees we detect any change to any code that the
    business logic can reach. Walking ~150 .py files + hashing in one
    SHA-1 stays well under 100ms on dev hardware.

    Files are sorted by relative path before hashing so the result is
    order-independent across filesystems.
    """
    app_root = Path(__file__).parent.parent  # backend/app/
    py_files = sorted(app_root.rglob("*.py"))
    h = hashlib.sha1()
    for f in py_files:
        # Hash both the path (so adding/removing files changes hash) and
        # the content (so editing changes hash). Using relative path keeps
        # the hash stable across machines with different cwds.
        rel = f.relative_to(app_root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        try:
            h.update(f.read_bytes())
        except OSError:
            # Unreadable file — record the fact, don't abort. A missing
            # file is itself a fingerprint signal.
            h.update(b"<unreadable>")
        h.update(b"\x01")
    return h.hexdigest()[:12]  # 12 hex chars is plenty for change detection


def _compute_git_sha() -> str:
    """Current git HEAD short SHA, or 'no-git' if not in a git repo.

    Must never raise — fingerprint must be obtainable even on a checkout
    without git installed, in a shallow clone, in a tarball deployment,
    or with a corrupted .git directory.
    """
    project_root = Path(__file__).parent.parent.parent.parent  # repo root
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            sha = result.stdout.strip()
            # Check for uncommitted modifications — if there are any, suffix
            # the SHA so it's obvious the runtime doesn't match git HEAD.
            dirty = subprocess.run(
                ["git", "-C", str(project_root), "status", "--porcelain"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if dirty.returncode == 0 and dirty.stdout.strip():
                return f"{sha}-dirty"
            return sha
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return "no-git"


def _detect_venv() -> dict:
    """Identify whether the current interpreter is the project venv.

    A common cause of the 2026-05-27 incident was system Python workers
    serving requests instead of `.venv/Scripts/python.exe`. We surface
    both the executable path AND a normalized "is_venv" boolean so the
    restart script can fail fast if it spawns the wrong interpreter.
    """
    # sys.base_prefix == sys.prefix on system Python; differs in venv.
    is_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    return {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "is_venv": is_venv,
        "sys_prefix": sys.prefix,
    }


def _compute_fingerprint() -> dict:
    """Build the fingerprint dict. Called once at module import."""
    venv = _detect_venv()
    # The reload mode is signalled by an env var the launcher sets, NOT
    # by inspecting uvicorn internals — those are private API. Restart
    # script controls the env var; absence means deterministic mode.
    reload_mode = os.environ.get("WB_RELOAD_MODE", "0") == "1"
    return {
        "boot_id": str(uuid.uuid4()),
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cwd": os.getcwd(),
        "reload_mode": reload_mode,
        # Identity of the interpreter — exposes "ran wrong python" bugs.
        **venv,
        # Identity of the code — exposes "ran old code" bugs.
        "pipeline_hash": _compute_pipeline_hash(),
        "git_sha": _compute_git_sha(),
    }


def get_fingerprint() -> dict:
    """Return the cached fingerprint dict.

    Idempotent within a process; first call computes, subsequent calls
    return the same dict. The dict is treated as immutable downstream —
    callers MUST NOT mutate it.
    """
    global _FINGERPRINT
    if _FINGERPRINT is None:
        _FINGERPRINT = _compute_fingerprint()
    return _FINGERPRINT


def boot_log_line() -> str:
    """One-line, grep-friendly boot fingerprint for the log file.

    Format intentionally keeps the keyword `[boot-fingerprint]` literal so
    operators can `grep boot-fingerprint pipeline.log` and see every
    worker's identity in chronological order, no JSON parsing required.
    """
    f = get_fingerprint()
    return (
        "[boot-fingerprint] "
        f"boot_id={f['boot_id']} pid={f['pid']} "
        f"python={f['python_executable']} is_venv={f['is_venv']} "
        f"reload={f['reload_mode']} "
        f"pipeline_hash={f['pipeline_hash']} git={f['git_sha']} "
        f"started_at={f['started_at']}"
    )
