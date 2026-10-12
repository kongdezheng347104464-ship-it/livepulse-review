"""Invariant harness — semantic guarantees, not endpoint coverage.

The risk this catches: analytics regressions that don't break /sessions
list-or-404 style smoke tests but quietly produce wrong numbers downstream
("composite_score 35.7 should have been 38.2 after the v5 refactor",
"intent-details started including gift messages after the source_type
re-plumb"). Endpoint tests don't see these — they ask "did I get a JSON
back". Invariant tests ask "is the JSON's content still meaningfully
correct under the rules we've already shipped?"

Each test asserts ONE semantic rule across ALL eligible sessions in the
dev DB. Parameterising via the live DB (not fixtures) means new sessions
added during product work are covered automatically — the harness gets
stronger as data accumulates, not weaker.

Run:
    make test-invariants                # backend must be running
    pytest tests/api/test_invariants.py -v     # equivalent
"""
from __future__ import annotations

import subprocess
import uuid as _uuid
from datetime import datetime
from pathlib import Path

import httpx
import pytest

# Targets the running dev backend rather than spinning a TestClient — keeps
# the harness honest about lifespan/middleware behaviour and avoids the
# pytest-asyncio + asyncpg event-loop quirks documented in
# tests/integration/conftest.py. Cost: backend must be up. `make
# test-invariants` enforces that for you.
BACKEND_URL = "http://127.0.0.1:8004"
_PROJECT_BACKEND = Path(__file__).resolve().parents[2]
_ENV_FILE = _PROJECT_BACKEND / ".env"


def _load_api_key() -> str:
    """Pull API_KEY from backend/.env so tests authenticate the same way
    the frontend does — never hardcode a key in test source.
    """
    if not _ENV_FILE.exists():
        pytest.exit(f"missing {_ENV_FILE}", returncode=2)
    for line in _ENV_FILE.read_text().splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip()
    pytest.exit("API_KEY not found in backend/.env", returncode=2)


API_KEY = _load_api_key()
HEADERS = {"X-API-Key": API_KEY}


def _psql(sql: str) -> str:
    """Run a SQL query via docker exec for DB-level invariants that have
    no API surface. Returns trimmed stdout. Tests that need this prefer
    it over an async SQLAlchemy session to keep the harness sync-simple.
    """
    out = subprocess.check_output(
        [
            "docker", "exec", "workbuddy_postgres",
            "psql", "-U", "trader", "-d", "livestream_analyzer",
            "-tA", "-c", sql,
        ],
        text=True, timeout=10,
    )
    return out.strip()


# ----- Fixtures --------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    """HTTP client pre-configured with API_KEY. Fails fast with a clear
    message if the backend isn't up — better than 10 confusing test
    failures from connection refused.
    """
    c = httpx.Client(base_url=BACKEND_URL, headers=HEADERS, timeout=30)
    try:
        r = c.get("/api/v1/health", timeout=5)
        r.raise_for_status()
    except Exception as e:
        c.close()
        pytest.exit(
            f"backend not reachable at {BACKEND_URL}: {e}. "
            f"Run `make backend-bg` first.",
            returncode=2,
        )
    yield c
    c.close()


@pytest.fixture(scope="module")
def analyzed_session_ids(client) -> list[str]:
    """All analyzed session IDs from the live DB. Module-scoped so we hit
    the list endpoint once per test run, not per test.

    Refuses to run vacuously: a test DB with zero analyzed sessions can't
    validate per-session invariants, so we exit instead of falsely
    passing every loop.
    """
    r = client.get("/api/v1/sessions/", params={"limit": 200})
    r.raise_for_status()
    items = r.json().get("data", {}).get("items", [])
    ids = [it["id"] for it in items if it.get("status") == "analyzed"]
    if not ids:
        pytest.exit(
            "no analyzed sessions in DB — invariants can't be validated. "
            "Run `make restore` first.",
            returncode=2,
        )
    return ids


# ===== Group 1: Auth boundaries ========================================
# Trivial to get right, catastrophic if regressed (DEBUG=true left on, or
# someone removes the dependency on validate_api_key).

class TestAuth:
    # Day 3 P0-2 (2026-05-31): business endpoints now accept EITHER
    # X-API-Key OR Bearer JWT. Missing both → 401 (was 403 under old
    # X-API-Key-only regime). Wrong X-API-Key falls through to JWT
    # which is also missing → still 401. Either way the request is
    # rejected — the assertion just records the new semantic class.
    def test_no_credentials_rejected(self):
        """invariant: missing both X-API-Key AND Bearer → 401."""
        r = httpx.get(f"{BACKEND_URL}/api/v1/sessions/", timeout=5)
        assert r.status_code == 401, f"got {r.status_code}: {r.text[:200]}"

    def test_wrong_api_key_rejected(self):
        """invariant: wrong X-API-Key alone falls through to JWT path
        which has no Bearer → 401. Never 200."""
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/sessions/",
            headers={"X-API-Key": "obviously-bogus-key"},
            timeout=5,
        )
        assert r.status_code == 401, f"got {r.status_code}: {r.text[:200]}"


# ===== Group 2: Score ranges & summary completeness ====================
# scoring_version migrations (v4 → v5 etc.) are the highest-risk class of
# change in this codebase. These invariants are the seatbelt for that.

class TestScores:
    def test_composite_score_in_range(self, client, analyzed_session_ids):
        """invariant: 0 <= composite_score <= 100 for every analyzed session.

        The 2026-05-27 incident wrote bogus composite_scores that happened
        to fall in range, so range alone isn't enough — but out-of-range
        IS a smoking gun for arithmetic / dimensional bugs.
        """
        # GET /sessions/{id} returns the session under `data.session` (sibling
        # to `data.danmakus` / `data.script_segments`), not flat at data root.
        # The flat root path silently returns None for everything, which
        # would let this test vacuously pass for the entire session pool.
        for sid in analyzed_session_ids:
            r = client.get(f"/api/v1/sessions/{sid}")
            sess = r.json().get("data", {}).get("session", {})
            assert sess, f"session {sid}: response missing data.session"
            score = sess.get("composite_score")
            assert score is None or 0 <= float(score) <= 100, \
                f"session {sid}: composite_score={score} out of [0, 100]"

    def test_all_subscores_in_range(self, client, analyzed_session_ids):
        """invariant: each sub-score (engagement / conversion / content)
        is in [0, 100], not just the composite.

        v5 (2026-05-29) introduced a blended engagement formula
        (normalized_rate × 0.6 + absolute × 0.4). Without per-sub-score
        bounds, a future formula tweak could produce an out-of-range
        sub-score that gets weighted-averaged into a still-in-range
        composite — silent corruption. This test makes that class of
        regression loud at the API surface.
        """
        required = ("engagement", "conversion", "content")
        checked = 0
        for sid in analyzed_session_ids:
            r = client.get(f"/api/v1/sessions/{sid}")
            sess = r.json().get("data", {}).get("session", {})
            scores = (sess.get("analysis_summary") or {}).get("scores") or {}
            if not scores:
                continue
            for k in required:
                v = scores.get(k)
                if v is None:
                    continue
                assert 0 <= float(v) <= 100, \
                    f"session {sid}: scores.{k}={v} outside [0, 100]"
            checked += 1
        assert checked > 0, "no session had a scores dict — invariant un-exercised"

    def test_analysis_summary_scores_complete(self, client, analyzed_session_ids):
        """invariant: analysis_summary.scores has all 4 keys AND top-level
        composite_score equals scores.composite (within float epsilon).

        Catches the 2026-05-27 class of bug: a stale worker wrote
        composite_score but forgot the `composite` key inside
        scores={engagement,conversion,content,...}, so the dashboard showed
        a different number from the top-level. The pipeline scoring step
        now asserts this on write — this test asserts it on read.
        """
        required = {"composite", "content", "engagement", "conversion"}
        checked = 0
        for sid in analyzed_session_ids:
            r = client.get(f"/api/v1/sessions/{sid}")
            sess = r.json().get("data", {}).get("session", {})
            summary = sess.get("analysis_summary") or {}
            if not summary:
                continue  # un-analyzed or partial — not our invariant
            scores = summary.get("scores") or {}
            missing = required - set(scores.keys())
            assert not missing, f"session {sid}: scores missing keys {missing} (have {set(scores.keys())})"
            top = summary.get("composite_score")
            inner = scores.get("composite")
            assert top is not None and inner is not None, \
                f"session {sid}: composite_score top={top} inner={inner}"
            assert abs(float(top) - float(inner)) < 0.05, \
                f"session {sid}: top composite {top} != inner {inner}"
            checked += 1
        # Refuse vacuous pass — at least one analyzed session must have a
        # populated analysis_summary, or we'd be silently green on a
        # broken pipeline.
        assert checked > 0, "no session had analysis_summary — invariant un-exercised"


# ===== Group 3: source_type invariants =================================
# The Q1 conversion invariant. Without it the funnel happily mixes "讲解
# 时长更长" speech with "送出了 520 的花" gifts and produces nonsense
# purchase-intent numbers.

class TestSourceType:
    def test_no_unknown_persisted(self):
        """invariant: source_type='unknown' must NEVER exist in DB.

        unknown is the classifier's diagnostic bucket — OCR noise that
        doesn't match the speech/gift/system grammar. _step_danmaku is
        contractually obliged to drop these before persistence (see
        pipeline.py:712-715). If this assertion ever fails, the pipeline
        drop is broken and downstream baselines are being silently
        polluted.
        """
        cnt = int(_psql("SELECT COUNT(*) FROM danmakus WHERE source_type='unknown';"))
        assert cnt == 0, (
            f"{cnt} unknown danmakus persisted — pipeline filter broken. "
            f"Sample: " + _psql(
                "SELECT id, content FROM danmakus WHERE source_type='unknown' LIMIT 3;"
            )
        )

    def test_danmaku_window_defaults_to_speech_only(self, client, analyzed_session_ids):
        """invariant: GET /danmaku-window without source_type param → only speech.

        Backend default is `source_type="speech"`. Removing the default
        would silently corrupt the conversion funnel for every UI client
        that doesn't explicitly opt-in. This test fails the moment that
        default regresses.
        """
        checked = 0
        for sid in analyzed_session_ids:
            r = client.get(
                f"/api/v1/sessions/{sid}/danmaku-window",
                params={"start_sec": 0, "end_sec": 99999, "limit": 100},
            )
            if r.status_code != 200:
                continue
            # Response key is `danmakus` (legacy soft-rename — see
            # models/danmaku.py header), not `items`. Easy footgun.
            items = r.json().get("data", {}).get("danmakus", [])
            if not items:
                continue
            non_speech = [d for d in items if d.get("source_type") != "speech"]
            assert not non_speech, (
                f"session {sid}: default returned {len(non_speech)} non-speech items, "
                f"first 3: {non_speech[:3]}"
            )
            checked += 1
        assert checked > 0, "no session had danmaku data — invariant un-exercised"

    def test_intent_details_speech_only(self, client, analyzed_session_ids):
        """invariant: intent-details only returns speech (defense in depth).

        Intent classification only runs on speech, so any non-speech in the
        response would mean the API joined wrong or someone bypassed the
        invariant. The endpoint adds an explicit `source_type='speech'`
        predicate; this test confirms it stays there.
        """
        checked = 0
        for sid in analyzed_session_ids:
            r = client.get(
                f"/api/v1/sessions/{sid}/intent-details",
                params={"intent": "all", "limit": 50},
            )
            if r.status_code != 200:
                continue
            items = r.json().get("data", {}).get("items", [])
            if not items:
                continue
            non_speech = [d for d in items if d.get("source_type") not in ("speech", None)]
            # None tolerated because older rows pre-source_type may have null;
            # the contract is "never non-speech", not "always speech".
            assert not non_speech, \
                f"session {sid}: intent-details leaked non-speech: {non_speech[:3]}"
            checked += 1
        # Don't require checked > 0 — sessions without intent results are fine


# ===== Group 4a: v5 normalized_metrics (Day-1 introduced) ===============
# These guard the new analysis_summary.normalized_metrics contract: when
# viewer_snapshots exist, engaged_audience_rate MUST be computable; when
# the rate is computable, it MUST be in a plausible range (>2.0 means
# ghost counting / OCR noise is severe and the metric is meaningless).

class TestNormalizedMetricsV5:
    def test_engaged_rate_present_when_viewer_snapshots_exist(
        self, client, analyzed_session_ids,
    ):
        """invariant: viewer_snapshots present → engaged_audience_rate non-None.

        The 2-source-of-truth concern: if viewer_snapshots has rows but
        normalized_metrics.engaged_audience_rate is None, the scoring step
        silently skipped the rate computation — Engaged Audience Rate
        baseline pool would degrade and we wouldn't notice.
        """
        checked = 0
        for sid in analyzed_session_ids:
            has_viewers = int(_psql(
                f"SELECT COUNT(*) FROM viewer_snapshots WHERE session_id='{sid}';"
            ))
            if has_viewers == 0:
                continue
            r = client.get(f"/api/v1/sessions/{sid}")
            sess = r.json().get("data", {}).get("session", {})
            summary = sess.get("analysis_summary") or {}
            nm = summary.get("normalized_metrics") or {}
            assert nm.get("engaged_audience_rate") is not None, (
                f"session {sid}: has {has_viewers} viewer snapshots but "
                f"engaged_audience_rate is None (nm={nm})"
            )
            checked += 1
        if checked == 0:
            pytest.skip("no session has viewer_snapshots — invariant un-exercised")

    def test_engaged_rate_in_plausible_range(self, client, analyzed_session_ids):
        """invariant: 0 < engaged_audience_rate <= 2.0 when set.

        Perfect cardinalization caps at 1.0 (every viewer talks exactly
        once with a unique handle). We allow up to 2.0 to absorb ghost
        counting from OCR (same person under multiple normalized handles
        because of UI variation). Anything beyond 2.0 means the
        normalization layer is leaking, not just over-counting.
        """
        checked = 0
        for sid in analyzed_session_ids:
            r = client.get(f"/api/v1/sessions/{sid}")
            sess = r.json().get("data", {}).get("session", {})
            summary = sess.get("analysis_summary") or {}
            nm = summary.get("normalized_metrics") or {}
            rate = nm.get("engaged_audience_rate")
            if rate is None:
                continue
            assert 0 < rate <= 2.0, (
                f"session {sid}: engaged_audience_rate={rate} outside (0, 2.0]"
            )
            checked += 1
        if checked == 0:
            pytest.skip("no session has engaged_audience_rate — invariant un-exercised")


# ===== Group 4b: Viewer math ============================================
# normalization metrics (Engaged Audience Rate, future Layer-5 state)
# divide by viewer_avg. Garbage in = silently wrong rates.

class TestViewerMath:
    def test_peak_gte_avg_when_both_present(self, client, analyzed_session_ids):
        """invariant: viewer_peak >= viewer_avg whenever both are set."""
        checked = 0
        for sid in analyzed_session_ids:
            r = client.get(f"/api/v1/sessions/{sid}")
            sess = r.json().get("data", {}).get("session", {})
            peak, avg = sess.get("viewer_peak"), sess.get("viewer_avg")
            if peak is None or avg is None:
                continue
            assert peak >= avg, f"session {sid}: peak={peak} < avg={avg}"
            checked += 1
        if checked == 0:
            pytest.skip("no session has viewer counts — invariant un-exercised")

    def test_avg_positive_when_set(self):
        """invariant: viewer_avg in DB is never zero when peak > 0.

        avg=0 with peak>0 means the divisor would be 0 when computing
        normalization metrics. We don't crash the upload path, but we do
        require the DB to refuse this combination implicitly through how
        the pipeline writes it. If this ever fails, viewer_count
        extraction is averaging an empty list somewhere.
        """
        rows = _psql(
            "SELECT id, viewer_avg, viewer_peak FROM live_sessions "
            "WHERE viewer_peak > 0 AND viewer_avg = 0;"
        )
        assert rows == "", f"sessions with peak>0 but avg=0: {rows}"


# ===== Group 4c: Pipeline integrity (added 2026-05-29 P0-8) ===========
# Each invariant here exists because a QA-discovered regression in the
# 2026-05-29 audit slipped past the existing harness. The pattern: fix
# bug → ship invariant in the SAME commit so the next regression of the
# same shape lands red instead of silent.

class TestPipelineIntegrity:
    def test_analyzed_session_has_step_rows(self, client, analyzed_session_ids):
        """invariant: every analyzed session has ≥ 1 row in pipeline_steps.

        Protects P-1 root cause from recurring. If somebody reverts
        step_tracker.commit() back to flush() the underlying observation
        — pipeline writes step rows during run — gets exercised here.
        Empty pipeline_steps for an analyzed session means either:
          (a) commit boundary regression (the QA P-1 bug)
          (b) someone migrated and forgot to backfill (operational)
        Both warrant a red test.
        """
        checked = 0
        for sid in analyzed_session_ids:
            n_steps = int(_psql(
                f"SELECT COUNT(*) FROM pipeline_steps WHERE session_id='{sid}';"
            ))
            assert n_steps > 0, (
                f"session {sid}: status=analyzed but 0 pipeline_steps rows. "
                "Either step_tracker commit broke (QA P-1) or session was "
                "analyzed before step tracking was introduced."
            )
            checked += 1
        if checked == 0:
            pytest.skip("no analyzed sessions — invariant un-exercised")

    def test_total_danmaku_matches_db_count(self, client, analyzed_session_ids):
        """invariant: analysis_summary danmaku count matches DB danmakus row count.

        Catches B-8 (QA 2026-05-29): OCR processed 581 speech events, the
        in-memory pipeline state set total_danmaku correctly, but DB had
        0 rows — pure silent drift between two sources of truth.
        We accept ±1 to account for rare race in re-rerun flows but
        anything wider is real drift.
        """
        checked = 0
        for sid in analyzed_session_ids:
            r = client.get(f"/api/v1/sessions/{sid}")
            sess = r.json().get("data", {}).get("session", {})
            summary_total = (sess.get("analysis_summary") or {}).get("total_danmaku")
            if summary_total is None:
                # Older sessions without this key — skip rather than flake.
                continue
            db_count = int(_psql(
                f"SELECT COUNT(*) FROM danmakus WHERE session_id='{sid}';"
            ))
            assert abs(summary_total - db_count) <= 1, (
                f"session {sid}: summary.total_danmaku={summary_total} vs "
                f"DB danmakus={db_count} (diff > 1 = B-8-class drift)"
            )
            checked += 1
        if checked == 0:
            pytest.skip("no analyzed sessions with total_danmaku — un-exercised")

    def test_no_engagement_insight_when_no_danmaku(
        self, client, analyzed_session_ids,
    ):
        """invariant: when DB danmakus=0, no insight has impact='engagement'.

        Catches R-6 (QA 2026-05-29): with 0 danmaku rows the report still
        showed "#1 1:00-2:30 持续互动高峰（90s, 弹幕 0 条）" — a virtual
        互动高峰 backed by 0 audience-side evidence. The P0-7 fix gates
        hot_segments / engagement_spikes on `has_danmaku_signal`; this
        invariant guards against regressing that gate.

        Engagement-class insights are: impact='engagement' OR
        text containing 互动高峰 / 弹幕 N 条 — we check the impact field
        which is the structured source of truth.
        """
        checked = 0
        for sid in analyzed_session_ids:
            db_count = int(_psql(
                f"SELECT COUNT(*) FROM danmakus WHERE session_id='{sid}';"
            ))
            if db_count > 0:
                continue
            r = client.get(f"/api/v1/sessions/{sid}")
            sess = r.json().get("data", {}).get("session", {})
            summary = sess.get("analysis_summary") or {}
            insights = (summary.get("insight_report") or {}).get("insights") or []
            engagement = [i for i in insights if i.get("impact") == "engagement"]
            assert engagement == [], (
                f"session {sid}: 0 danmakus in DB but {len(engagement)} "
                f"engagement insights present (R-6 假洞察 regression): "
                f"{[i.get('text', '?')[:30] for i in engagement[:3]]}"
            )
            checked += 1
        if checked == 0:
            pytest.skip("no zero-danmaku analyzed sessions — un-exercised")

    def test_embedding_cache_folder_not_windows_path(self):
        """invariant: embedding_service.py must not contain any string
        literal resembling a Windows drive-letter path.

        Catches B-6 (QA 2026-05-29): cache_folder='f:/models' silently
        failed on Mac → SentenceTransformer re-downloaded from HF Hub
        → cdn-lfs blocked → transformers fallback path triggered NameError
        → embedding pipeline silently degraded. Source check, not
        runtime — the runtime symptoms manifest 22 minutes deep.

        We scan all string literals because the variable was extracted to
        _MODEL_CACHE_DIR in the fix, but a future regression could either
        re-inline a literal or reassign the variable.
        """
        src = (_PROJECT_BACKEND / "app/services/embedding/embedding_service.py").read_text()
        import re
        # Strip line comments first — historical "f:/models" reference in
        # the explanation comment shouldn't trigger this. Block strings
        # (triple-quoted docstrings) are harder to strip with regex; we
        # accept the false-positive risk there as low because docstrings
        # rarely contain drive-letter paths.
        code_only = "\n".join(
            line.split("#", 1)[0] for line in src.splitlines()
        )
        winpath = re.findall(r'["\']([a-zA-Z]:[/\\][^"\']*)["\']', code_only)
        assert not winpath, (
            f"Windows-style path literal in embedding_service.py: {winpath}. "
            "Mac/Linux will silently fail. Use Path.home() or env var."
        )


# ===== Group 5: Null safety ============================================
# normalized_username is null for invalid/anonymous events (by design).
# Every consumer must handle that — past UI bugs treated null as a string
# and crashed.

class TestNullSafety:
    def test_responses_serialize_with_null_normalized_username(
        self, client, analyzed_session_ids,
    ):
        """invariant: API doesn't 500 on rows with normalized_username=NULL.

        Probes the endpoints that surface this field. We don't make
        content claims — only that the response is serializable.
        """
        # Pick a session that actually has null normalized_username so the
        # test is exercising the codepath, not vacuously passing.
        sample = _psql(
            "SELECT session_id FROM danmakus "
            "WHERE normalized_username IS NULL LIMIT 1;"
        )
        if not sample:
            pytest.skip("no rows with normalized_username=NULL — invariant un-exercised")
        sid = sample
        for path, params in [
            (f"/api/v1/sessions/{sid}", {}),
            (f"/api/v1/sessions/{sid}/danmaku-window",
             {"start_sec": 0, "end_sec": 99999, "limit": 100, "source_type": "all"}),
            # intent-details requires limit >= 5 per pydantic validator.
            (f"/api/v1/sessions/{sid}/intent-details", {"intent": "all", "limit": 50}),
        ]:
            r = client.get(path, params=params)
            assert r.status_code in (200, 404), \
                f"{path} → {r.status_code} on null normalized_username: {r.text[:200]}"


# ===== Group 6: Anchor upload XOR contract =============================
# `anchor_id` and `new_anchor_name` MUST be mutually exclusive AND
# exactly one MUST be provided. The contract enforces explicit identity
# attach/create — silent auto-create caused the M-15 identity
# fragmentation incident.

class TestAnchorUploadContract:
    # Tiny fake mp4 payload — enough to pass FastAPI multipart parsing
    # but rejected at content_type check. We never actually want this
    # file to land on disk; the XOR check runs BEFORE content_type
    # validation in upload.py.
    _FAKE_MP4 = (
        "test.mp4",
        b"\x00\x00\x00\x18ftypmp42",
        "video/mp4",
    )

    def _post(self, client, **data):
        return client.post(
            "/api/v1/upload/video",
            files={"file": self._FAKE_MP4},
            data={"title": "invariant-harness", **data},
        )

    def test_both_provided_returns_400(self, client):
        """invariant: anchor_id + new_anchor_name together → 400."""
        r = self._post(client, anchor_id="some-uuid", new_anchor_name="some-name")
        assert r.status_code == 400, f"got {r.status_code}: {r.text[:300]}"
        assert "exactly one" in r.text.lower() or "exactly one" in r.json().get("detail", "").lower()

    def test_neither_provided_returns_400(self, client):
        """invariant: missing both → 400 (no silent auto-create)."""
        r = self._post(client)
        assert r.status_code == 400, f"got {r.status_code}: {r.text[:300]}"


# ===== Group 7: 404 handling ===========================================

class TestNotFound:
    def test_missing_session_returns_404_not_500(self, client):
        """invariant: GET /sessions/{nonexistent-uuid} → 404, never 500."""
        fake = str(_uuid.uuid4())
        r = client.get(f"/api/v1/sessions/{fake}")
        assert r.status_code == 404, \
            f"expected 404, got {r.status_code}: {r.text[:200]}"


# ===== Group 9: Workspace API contract ==================================
# Replay Timeline Workspace bundle + notes CRUD. Invariants here are the
# *contract* the frontend depends on — they'd silently degrade Workspace
# UX if violated (timeline panel missing markers, transcript misaligned
# with currentTime, note save going to /dev/null).

# Marker text used by note-mutating tests so the cleanup fixture can find
# and remove only its own writes — never touches real user notes.
_HARNESS_NOTE_TAG = "[invariant-harness]"


@pytest.fixture
def cleanup_harness_notes():
    """Remove any notes this harness created during the test.

    Runs after every test that uses it, even on assertion failure. The
    LIKE pattern is intentionally specific so a misspelled tag would
    fail to clean up rather than risk deleting real notes.
    """
    yield
    _psql(
        f"DELETE FROM session_notes WHERE note_text LIKE '%{_HARNESS_NOTE_TAG}%';"
    )


class TestWorkspaceTimeline:
    REQUIRED_KEYS = {
        "session", "video_url", "viewer_snapshots",
        "insights", "notes", "segment_count",
    }

    def test_timeline_bundle_shape_for_any_analyzed_session(
        self, client, analyzed_session_ids,
    ):
        """invariant: /timeline returns the contract the Workspace expects.

        The Workspace renders nothing if any of these keys are missing,
        so this is a structural guard against accidental refactors that
        drop a field.
        """
        sid = analyzed_session_ids[0]
        r = client.get(f"/api/v1/sessions/{sid}/timeline")
        assert r.status_code == 200, r.text
        data = r.json().get("data", {})
        missing = self.REQUIRED_KEYS - set(data.keys())
        assert not missing, f"timeline bundle missing keys: {missing}"
        # session.id MUST match — otherwise frontend would render the
        # wrong session's metadata header.
        assert data["session"]["id"] == sid

    def test_timeline_insights_have_anchored_timestamps(
        self, client, analyzed_session_ids,
    ):
        """invariant: each insight marker has a start_sec int.

        The ▲ marker on the timeline is positioned via start_sec /
        duration_sec. A missing or wrong-typed value collapses the
        marker to position 0 — visually broken.
        """
        checked = 0
        for sid in analyzed_session_ids:
            r = client.get(f"/api/v1/sessions/{sid}/timeline")
            data = r.json().get("data", {})
            duration = data["session"]["duration_sec"]
            insights = data["insights"]
            for ins in insights:
                assert isinstance(ins["start_sec"], int), \
                    f"{sid}: insight start_sec={ins['start_sec']!r} not int"
                assert 0 <= ins["start_sec"] <= duration, \
                    f"{sid}: insight start_sec={ins['start_sec']} outside [0, {duration}]"
                checked += 1
        if checked == 0:
            pytest.skip("no insights across the session pool")

    def test_timeline_404_on_missing_session(self, client):
        """invariant: missing session → 404 (not 500, not empty 200)."""
        r = client.get(f"/api/v1/sessions/{_uuid.uuid4()}/timeline")
        assert r.status_code == 404

    def test_video_url_supports_range_or_is_null(
        self, client, analyzed_session_ids,
    ):
        """invariant: video_url is either null OR returns 206 on Range GET.

        The Workspace's scrub-always-paused interaction model depends on
        HTTP Range support. If MinIO ever stops honouring Range (config
        regression, proxy stripping headers), video seek is broken.
        """
        for sid in analyzed_session_ids:
            r = client.get(f"/api/v1/sessions/{sid}/timeline")
            url = r.json().get("data", {}).get("video_url")
            if not url:
                continue
            # Issue a Range request without auth — the URL is presigned.
            rng = httpx.get(url, headers={"Range": "bytes=0-1023"}, timeout=5)
            assert rng.status_code == 206, (
                f"session {sid}: video Range returned {rng.status_code}, "
                f"expected 206. MinIO Range support broken?"
            )
            # Don't need to check all — first session that has a video is enough.
            return
        pytest.skip("no session in pool has a video_url")


# ===== Group 10: 高光时刻 / Highlights (Sprint 6A) ======================
# The "高光时刻" tab lists shareable highlights (score>=60) and exports a
# clip per highlight. These invariants pin the two product rules that would
# silently break the feature: (1) the score>=60 gate must hold — a leaked
# sub-60 / negative insight would surface "你这段翻车" as a shareable
# highlight; (2) export must return a real, downloadable clip.


@pytest.fixture(scope="module")
def session_with_highlights(client, analyzed_session_ids) -> str | None:
    """First analyzed session that has ≥1 shareable highlight, or None.

    ~36% of sessions legitimately have none (empty is a normal state), so
    the export tests skip rather than fail when the pool happens to be all
    empty.
    """
    for sid in analyzed_session_ids:
        r = client.get(f"/api/v1/sessions/{sid}/highlights")
        if r.status_code == 200 and r.json().get("data", {}).get("count", 0) > 0:
            return sid
    return None


class TestHighlights:
    def test_score_gate_and_shape(self, client, analyzed_session_ids):
        """invariant: every listed highlight clears score>=60, and its id is
        its start_sec, sorted strongest-first.

        The score gate is the whole product safety story (ADR §3): drop it
        and negative "翻车" insights leak into the shareable list.
        """
        for sid in analyzed_session_ids:
            r = client.get(f"/api/v1/sessions/{sid}/highlights")
            assert r.status_code == 200, f"{sid}: {r.status_code} {r.text[:200]}"
            items = r.json()["data"]["items"]
            scores = [h["score"] for h in items]
            for h in items:
                assert h["score"] >= 60, f"{sid}: leaked sub-60 highlight {h}"
                assert h["id"] == str(h["start_sec"]), \
                    f"{sid}: id {h['id']!r} != start_sec {h['start_sec']}"
                assert h["end_sec"] >= h["start_sec"], f"{sid}: end < start {h}"
            assert scores == sorted(scores, reverse=True), \
                f"{sid}: highlights not sorted strongest-first: {scores}"

    def test_404_on_missing_session(self, client):
        """invariant: highlights of a missing session → 404 (gate fires)."""
        r = client.get(f"/api/v1/sessions/{_uuid.uuid4()}/highlights")
        assert r.status_code == 404

    def test_export_unknown_highlight_404(self, client, session_with_highlights):
        """invariant: exporting an id not in the score>=60 list → 404,
        never a clip. Guards the 'highlight_id-only, no arbitrary range'
        contract (ADR §4)."""
        if session_with_highlights is None:
            pytest.skip("no session with highlights in pool")
        r = client.post(
            f"/api/v1/sessions/{session_with_highlights}/highlights/99999999/export"
        )
        assert r.status_code == 404

    def test_export_produces_downloadable_clip(self, client, session_with_highlights):
        """invariant: exporting a real highlight returns a presigned clip
        URL that actually downloads as non-empty video.

        End-to-end: hits ffmpeg (-c copy, HTTP-seek) + MinIO round-trip.
        """
        if session_with_highlights is None:
            pytest.skip("no session with highlights in pool")
        hl = client.get(
            f"/api/v1/sessions/{session_with_highlights}/highlights"
        ).json()["data"]["items"][0]

        r = client.post(
            f"/api/v1/sessions/{session_with_highlights}/highlights/{hl['id']}/export"
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["expires_in"] > 0
        clip_url = data["clip_url"]
        assert clip_url.startswith("http")

        # The URL must actually resolve to a non-trivial video object.
        dl = httpx.get(clip_url, timeout=30)
        assert dl.status_code == 200, f"clip download {dl.status_code}"
        assert len(dl.content) > 10_000, f"clip suspiciously small: {len(dl.content)} bytes"


class TestWorkspaceTranscript:
    def test_transcript_window_overlap_semantics(self, client, analyzed_session_ids):
        """invariant: returned segments overlap the requested [start, end) window.

        Overlap (not containment) is the contract — a segment straddling
        a window boundary stays visible. Strict containment would hide
        the long segment that brackets the user's currentTime.
        """
        for sid in analyzed_session_ids:
            r = client.get(
                f"/api/v1/sessions/{sid}/transcript",
                params={"start_sec": 60, "end_sec": 90},
            )
            assert r.status_code == 200
            segments = r.json().get("data", {}).get("segments", [])
            for s in segments:
                # Overlap check: s.start < window.end AND s.end > window.start
                assert s["start_sec"] < 90 and s["end_sec"] > 60, (
                    f"{sid}: segment [{s['start_sec']}, {s['end_sec']}] "
                    f"doesn't overlap [60, 90)"
                )
            if segments:
                return  # first session with hits is enough
        pytest.skip("no session has segments in the 60-90s test window")

    def test_transcript_rejects_invalid_window(self, client, analyzed_session_ids):
        """invariant: start_sec >= end_sec → 400, not silent empty list."""
        sid = analyzed_session_ids[0]
        r = client.get(
            f"/api/v1/sessions/{sid}/transcript",
            params={"start_sec": 100, "end_sec": 50},
        )
        assert r.status_code == 400, f"got {r.status_code}: {r.text[:200]}"


# ===== Group 11: Dashboard 聚合 ========================================
# 首页"复盘中控台"喂数接口。一次请求出全部聚合，服务端算（含可导出高光
# JSONB 聚合）。这些 invariant 钉死契约形状 + 数值合理，避免首页静默错乱。

class TestDashboard:
    REQUIRED_KEYS = {
        "in_progress", "this_month", "exportable_highlights",
        "recent_reports", "frequent_anchors",
    }

    def test_shape(self, client):
        """invariant: /dashboard 返回首页依赖的完整契约。"""
        r = client.get("/api/v1/dashboard")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        missing = self.REQUIRED_KEYS - set(data.keys())
        assert not missing, f"dashboard 缺字段: {missing}"
        assert isinstance(data["in_progress"], list)
        assert isinstance(data["recent_reports"], list)
        assert isinstance(data["frequent_anchors"], list)
        tm = data["this_month"]
        assert isinstance(tm["review_count"], int) and tm["review_count"] >= 0
        assert tm["avg_score"] is None or isinstance(tm["avg_score"], (int, float))

    def test_exportable_highlights_nonneg_int(self, client):
        """invariant: 可导出高光总数是非负整数（JSONB 聚合不该出负/None）。"""
        data = client.get("/api/v1/dashboard").json()["data"]
        n = data["exportable_highlights"]
        assert isinstance(n, int) and n >= 0, f"exportable_highlights={n!r}"

    def test_recent_reports_are_analyzed(self, client):
        """invariant: 最近报告只含已分析场次（有综合分），≤5 条。

        守"不伪造"：未分析的场次不该混进"最近报告"。
        """
        reports = client.get("/api/v1/dashboard").json()["data"]["recent_reports"]
        assert len(reports) <= 5
        for rep in reports:
            assert {"session_id", "title", "anchor_name", "date", "composite_score"} <= set(rep.keys())

    def test_frequent_anchors_sorted_desc(self, client):
        """invariant: 常用主播按场次数倒序，≤5 条。"""
        anchors = client.get("/api/v1/dashboard").json()["data"]["frequent_anchors"]
        assert len(anchors) <= 5
        counts = [a["session_count"] for a in anchors]
        assert counts == sorted(counts, reverse=True), f"未按场次数倒序: {counts}"


class TestWorkspaceRangeAnalysis:
    """Range-analysis endpoint contract — validation paths only.

    We deliberately DON'T call Claude in these tests (would need a real
    API key, costs money, slow). The 503 path (no key configured) is
    actually our happy-path invariant for "endpoint refuses cleanly"
    in a no-key environment, and rejection paths (400/404/422) run
    without needing Claude at all.
    """

    def _first_segment_id(self, client, sid):
        r = client.get(
            f"/api/v1/sessions/{sid}/transcript",
            params={"start_sec": 0, "end_sec": 30},
        )
        segs = r.json().get("data", {}).get("segments", [])
        return segs[0]["id"] if segs else None

    def test_empty_segment_ids_rejected(self, client, analyzed_session_ids):
        """invariant: zero-length segment_ids → 422."""
        sid = analyzed_session_ids[0]
        r = client.post(
            f"/api/v1/sessions/{sid}/range-analysis",
            json={"segment_ids": []},
        )
        assert r.status_code == 422, r.text

    def test_too_many_segment_ids_rejected(self, client, analyzed_session_ids):
        """invariant: >60 segment_ids → 422 (Pydantic max_length).

        The 60 cap is the user-decided "around 10-20 min content" upper
        bound. Crossing it both inflates Claude prompt cost and dilutes
        attention.
        """
        sid = analyzed_session_ids[0]
        r = client.post(
            f"/api/v1/sessions/{sid}/range-analysis",
            json={"segment_ids": ["x"] * 61},
        )
        assert r.status_code == 422, r.text

    def test_unknown_session_returns_404(self, client):
        """invariant: missing session → 404, not 500."""
        r = client.post(
            f"/api/v1/sessions/{_uuid.uuid4()}/range-analysis",
            json={"segment_ids": ["any"]},
        )
        assert r.status_code == 404, r.text

    def test_foreign_segment_id_rejected(self, client, analyzed_session_ids):
        """invariant: segment_id that doesn't belong to this session → 422.

        Prevents a stale-cache or copy-paste bug from leaking another
        session's segments into the analysis prompt.
        """
        sid = analyzed_session_ids[0]
        # Use a well-formed UUID that doesn't exist in any session
        r = client.post(
            f"/api/v1/sessions/{sid}/range-analysis",
            json={"segment_ids": [str(_uuid.uuid4())]},
        )
        assert r.status_code == 422, r.text
        # The error should mention the bad id so the frontend can debug
        # without server access. Truncated text check, not exact equality.
        assert "not found" in r.text.lower()

    def test_analyses_list_404_on_missing_session(self, client):
        r = client.get(f"/api/v1/sessions/{_uuid.uuid4()}/range-analyses")
        assert r.status_code == 404


class TestWorkspaceNotes:
    def test_create_then_list_roundtrip(
        self, client, analyzed_session_ids, cleanup_harness_notes,
    ):
        """invariant: POST /notes creates a row that appears in GET /notes,
        with the same timestamp and text the client submitted.

        The Workspace stores note save success state based on the POST
        response, then re-renders the timeline ★ markers from a fresh
        GET. If either side drifts, markers and editor disagree.
        """
        sid = analyzed_session_ids[0]
        payload = {
            "timestamp_sec": 42,
            "note_text": f"roundtrip test {_HARNESS_NOTE_TAG}",
        }
        post_r = client.post(f"/api/v1/sessions/{sid}/notes", json=payload)
        assert post_r.status_code == 200, post_r.text
        created = post_r.json()["data"]
        assert created["timestamp_sec"] == 42
        assert created["note_text"] == payload["note_text"]

        list_r = client.get(f"/api/v1/sessions/{sid}/notes")
        items = list_r.json()["data"]["items"]
        found = [it for it in items if it["id"] == created["id"]]
        assert len(found) == 1, "newly-created note not in list response"
        assert found[0]["timestamp_sec"] == 42
        assert found[0]["note_text"] == payload["note_text"]

    def test_note_text_empty_or_whitespace_rejected(
        self, client, analyzed_session_ids,
    ):
        """invariant: blank notes → 422, never silently persist."""
        sid = analyzed_session_ids[0]
        # Pure whitespace (passes Pydantic min_length=1 but trims empty)
        r = client.post(
            f"/api/v1/sessions/{sid}/notes",
            json={"timestamp_sec": 0, "note_text": "   \n\t  "},
        )
        assert r.status_code == 422, r.text

    def test_edit_then_list_reflects_change(
        self, client, analyzed_session_ids, cleanup_harness_notes,
    ):
        """invariant: PATCH updates note_text and GET /notes shows the
        new text. v1.5 (2026-05-29) added inline note editing — this
        guards the read-after-write coherence the UI depends on."""
        sid = analyzed_session_ids[0]
        # Create
        created = client.post(
            f"/api/v1/sessions/{sid}/notes",
            json={"timestamp_sec": 7, "note_text": f"original {_HARNESS_NOTE_TAG}"},
        ).json()["data"]
        # Edit
        edited = client.patch(
            f"/api/v1/sessions/{sid}/notes/{created['id']}",
            json={"note_text": f"edited {_HARNESS_NOTE_TAG}"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["data"]["note_text"].startswith("edited")
        # Read-back via list
        listed = client.get(f"/api/v1/sessions/{sid}/notes").json()["data"]["items"]
        match = [n for n in listed if n["id"] == created["id"]]
        assert len(match) == 1
        assert match[0]["note_text"].startswith("edited")
        # Timestamp must be preserved — we deliberately don't allow
        # re-anchoring via PATCH, see UpdateNoteRequest docstring.
        assert match[0]["timestamp_sec"] == 7

    def test_delete_removes_from_list(
        self, client, analyzed_session_ids, cleanup_harness_notes,
    ):
        """invariant: DELETE returns 204 and the note disappears from
        the list — and a follow-up GET on its id (if we had one) would
        be 404."""
        sid = analyzed_session_ids[0]
        created = client.post(
            f"/api/v1/sessions/{sid}/notes",
            json={"timestamp_sec": 9, "note_text": f"to-delete {_HARNESS_NOTE_TAG}"},
        ).json()["data"]
        del_r = client.delete(f"/api/v1/sessions/{sid}/notes/{created['id']}")
        assert del_r.status_code == 204, del_r.text
        listed = client.get(f"/api/v1/sessions/{sid}/notes").json()["data"]["items"]
        assert not any(n["id"] == created["id"] for n in listed)

    def test_cross_session_edit_rejected(
        self, client, analyzed_session_ids, cleanup_harness_notes,
    ):
        """invariant: PATCH /sessions/A/notes/{note_owned_by_B} → 404,
        not "silently edit B's note". The session id in the path is a
        defense against tampering with notes from other sessions even
        with a known note_id."""
        if len(analyzed_session_ids) < 2:
            pytest.skip("need at least 2 sessions for cross-session test")
        sid_a, sid_b = analyzed_session_ids[0], analyzed_session_ids[1]
        # Create a note in session A
        created = client.post(
            f"/api/v1/sessions/{sid_a}/notes",
            json={"timestamp_sec": 3, "note_text": f"in A {_HARNESS_NOTE_TAG}"},
        ).json()["data"]
        # Try to PATCH it through session B's URL
        r = client.patch(
            f"/api/v1/sessions/{sid_b}/notes/{created['id']}",
            json={"note_text": f"hijack {_HARNESS_NOTE_TAG}"},
        )
        assert r.status_code == 404
        # Same for DELETE
        r2 = client.delete(f"/api/v1/sessions/{sid_b}/notes/{created['id']}")
        assert r2.status_code == 404

    def test_empty_edit_rejected(
        self, client, analyzed_session_ids, cleanup_harness_notes,
    ):
        """invariant: PATCH with whitespace-only note_text → 422."""
        sid = analyzed_session_ids[0]
        created = client.post(
            f"/api/v1/sessions/{sid}/notes",
            json={"timestamp_sec": 5, "note_text": f"x {_HARNESS_NOTE_TAG}"},
        ).json()["data"]
        r = client.patch(
            f"/api/v1/sessions/{sid}/notes/{created['id']}",
            json={"note_text": "   \n  "},
        )
        assert r.status_code == 422

    def test_timestamp_out_of_bounds_rejected(
        self, client, analyzed_session_ids,
    ):
        """invariant: timestamp_sec > session.duration_sec → 422.

        Off-the-end timestamps are almost always a frontend bug (stale
        currentTime, wrong session context). Better to refuse than
        silently store a marker that can't be reached.
        """
        sid = analyzed_session_ids[0]
        # Pull duration to set a guaranteed-too-large timestamp.
        sess = client.get(f"/api/v1/sessions/{sid}").json()["data"]["session"]
        duration = sess["duration_sec"] or 0
        if duration == 0:
            pytest.skip(f"session {sid} has no duration to bound against")
        r = client.post(
            f"/api/v1/sessions/{sid}/notes",
            json={
                "timestamp_sec": duration + 10_000,
                "note_text": f"out of bounds {_HARNESS_NOTE_TAG}",
            },
        )
        assert r.status_code == 422, r.text


# ===== Group 8: /health runtime fingerprint =============================
# Restart scripts and post-mortem grep depend on these fields being
# present and parseable. Any change that drops one of them silently
# breaks `restart_backend.sh` polling.

class TestHealthRuntime:
    def test_runtime_fields_present_and_parseable(self, client):
        """invariant: /health.data.runtime has boot_id (UUID), git_sha
        (non-empty), started_at (ISO-8601), pid (int)."""
        r = client.get("/api/v1/health")
        r.raise_for_status()
        runtime = r.json().get("data", {}).get("runtime", {})
        _uuid.UUID(runtime["boot_id"])  # raises if invalid
        sha = runtime["git_sha"]
        assert sha, "git_sha empty"
        # Allow `-dirty` suffix and the special `no-git` sentinel — both
        # are valid states the fingerprint module documents.
        datetime.fromisoformat(runtime["started_at"])
        assert isinstance(runtime["pid"], int) and runtime["pid"] > 0


# ===== Group 9: B5 真分层 早期 insights 契约 ==========================
# (2026-05-30) early_insights 维度必须与 full insights 的 impact 集合
# 无交集。一旦交集就是"Early 说成交弱 / Full 说成交强"那种信任崩盘的
# 失败模式 (AI 守则 #3)。

class TestEarlyInsightContract:
    def test_early_dimensions_never_overlap_full_impacts(self):
        """invariant: early_insights[].dimension 与 full insights[].impact
        集合永不相交。Early 只看脚本，Full 含弹幕/转化/互动；混在一起会
        让用户在 5 分钟和 20 分钟看到矛盾结论。
        """
        from app.services.insight_engine.early_threshold_sets import (
            EARLY_THRESHOLD_SETS,
        )
        early_dims = {
            s["dimension_label"]
            for s in EARLY_THRESHOLD_SETS["general_v1"]["signals"].values()
        }
        # Full insights 用的 impact 集合 (insight_generator.py 里硬编码的
        # 几个枚举值)，加上中文别名的可能拼写
        full_impacts = {
            "conversion", "engagement", "rhythm",
            "互动", "转化", "成交", "购买意图", "留存", "观众质量",
        }
        overlap = early_dims & full_impacts
        assert not overlap, (
            f"Early dimensions overlap with full impacts: {overlap}. "
            f"This breaks the B5 separation contract (AI 守则 #3)."
        )

    def test_general_v1_threshold_set_is_frozen(self):
        """invariant: published threshold_sets are immutable. If you
        intentionally need to change v1, bump to v2 in code and keep v1
        intact — do NOT flip frozen to False.
        """
        from app.services.insight_engine.early_threshold_sets import (
            EARLY_THRESHOLD_SETS,
        )
        assert EARLY_THRESHOLD_SETS["general_v1"].get("frozen") is True

    def test_action_registry_is_frozen(self):
        from app.services.insight_engine.early_action_registry import load_registry
        reg = load_registry()
        assert reg["_meta"].get("frozen") is True


# ===== Group 10: A1 Auth route coverage（2026-05-31 Day 3 P1）===========
# 防呆：扫描 app.routes，确保除白名单外每个 endpoint 的 dependency tree
# 里都接了 auth dep（get_admin_or_user / get_current_user* / validate_api_key）。
# 一旦未来新加 endpoint 忘记鉴权 = CI 红。

# (method, path) 白名单 — 必须人工 review 才能往这里加新条目
PUBLIC_ROUTES: set[tuple[str, str]] = {
    ("GET",  "/api/v1/health"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
}

# 任一名字命中 = 该 endpoint 已鉴权
AUTH_DEP_NAMES: set[str] = {
    "get_admin_or_user",     # 业务 endpoint 标准 dep
    "get_current_user",      # 仅 JWT 用户（暂无 endpoint 用，但保留兼容）
    "get_current_user_full", # /auth/me 专用
    "require_admin",         # 严格 admin-only
    "validate_api_key",      # _debug 路径
}


def _all_dep_names(route) -> set[str]:
    """递归收集 route.dependant 树里的所有 callable 名字。"""
    out: set[str] = set()
    def visit(d):
        if d.call and hasattr(d.call, "__name__"):
            out.add(d.call.__name__)
        for sd in d.dependencies:
            visit(sd)
    if hasattr(route, "dependant"):
        for d in route.dependant.dependencies:
            visit(d)
        # Body-bound dependencies (rare) also count
        for d in getattr(route.dependant, "body_params", []) or []:
            pass
    return out


class TestAuthRouteCoverage:
    def test_every_non_public_route_has_auth_dep(self):
        """invariant: 任何不在 PUBLIC_ROUTES 白名单里的 endpoint，
        其 dependency tree 必须包含至少一个 AUTH_DEP_NAMES 里的名字。
        加 endpoint 忘记 Depends(get_admin_or_user) = 这条 test 红。"""
        from fastapi.routing import APIRoute
        from app.main import app

        violations = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            methods = route.methods - {"HEAD", "OPTIONS"}
            if not methods:
                continue
            for method in methods:
                key = (method, route.path)
                if key in PUBLIC_ROUTES:
                    continue
                deps = _all_dep_names(route)
                if not (deps & AUTH_DEP_NAMES):
                    violations.append(f"{method} {route.path}  deps={sorted(deps)}")

        assert not violations, (
            f"{len(violations)} unauthenticated business routes found:\n  " +
            "\n  ".join(violations) +
            "\n\nFix: add `dependencies=[Depends(get_admin_or_user)]` to the router, "
            "or add (method, path) to PUBLIC_ROUTES whitelist if intentional."
        )

    def test_public_routes_actually_exist(self):
        """Catches drift in the white-list: if we delete /health but forget
        to remove it from PUBLIC_ROUTES, the whitelist silently rots."""
        from fastapi.routing import APIRoute
        from app.main import app

        existing: set[tuple[str, str]] = set()
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            for m in (route.methods - {"HEAD", "OPTIONS"}):
                existing.add((m, route.path))
        missing = PUBLIC_ROUTES - existing
        assert not missing, f"PUBLIC_ROUTES references nonexistent routes: {missing}"


# ===== Group 11: A1 Auth security invariants (2026-05-31 Day 5) ========
# Higher-level than the endpoint integration tests in test_auth_endpoints.py:
# these guard 安全契约 that must hold against the LIVE backend, even after
# refactors that may pass the unit suite. Failure = security regression.

import time as _time
import uuid as _uuid
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

import jwt as _pyjwt
import psycopg2 as _psycopg2

from app.config import get_settings as _get_settings
from app.services.auth.jwt_service import JWT_ALG as _JWT_ALG


def _pg_conn():
    raw = _get_settings().database_url
    return _psycopg2.connect(raw.replace("postgresql+asyncpg://", "postgresql://", 1))


def _seed_invitation_for_test() -> str:
    code = _uuid.uuid4().hex[:16]
    with _pg_conn() as c:
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
            admin_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO invitations (code, created_by, created_at) "
                "VALUES (%s, %s, NOW())",
                (code, admin_id),
            )
    return code


def _cleanup_test_artifacts(code: str | None, email: str | None):
    with _pg_conn() as c:
        c.autocommit = True
        with c.cursor() as cur:
            if code:
                cur.execute("DELETE FROM invitations WHERE code=%s", (code,))
            if email:
                cur.execute("DELETE FROM users WHERE LOWER(email)=LOWER(%s)", (email,))


class TestAuthSecurityInvariants:
    BACKEND = BACKEND_URL

    # --- token validity ---
    def test_missing_token_protected_endpoint_returns_401(self):
        r = httpx.get(f"{self.BACKEND}/api/v1/auth/me", timeout=5)
        assert r.status_code == 401

    def test_garbage_token_returns_401(self):
        r = httpx.get(
            f"{self.BACKEND}/api/v1/auth/me",
            headers={"Authorization": "Bearer obviously.not.a.jwt"},
            timeout=5,
        )
        assert r.status_code == 401

    def test_expired_token_returns_401(self):
        """Forge a token with iat/exp in the past, signed with the
        real JWT_SECRET. Server must reject on exp check."""
        settings = _get_settings()
        past = _dt.now(_tz.utc) - _td(days=10)
        payload = {
            "sub": "00000000-0000-0000-0000-000000000000",
            "iat": int(past.timestamp()),
            # exp 3 days ago — past whatever the lib's default leeway is
            "exp": int((past + _td(days=7)).timestamp()),
            "jti": "expired-test",
        }
        token = _pyjwt.encode(payload, settings.jwt_secret, algorithm=_JWT_ALG)
        r = httpx.get(
            f"{self.BACKEND}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        assert r.status_code == 401

    # --- register policy enforcement ---
    def test_weak_password_register_returns_422(self):
        code = _seed_invitation_for_test()
        try:
            r = httpx.post(
                f"{self.BACKEND}/api/v1/auth/register",
                json={"email": f"weak-{_uuid.uuid4().hex[:6]}@x.com",
                      "password": "short1", "invitation_code": code},
                timeout=5,
            )
            assert r.status_code == 422
        finally:
            _cleanup_test_artifacts(code, None)

    def test_invalid_invitation_register_returns_422(self):
        r = httpx.post(
            f"{self.BACKEND}/api/v1/auth/register",
            json={"email": f"noinv-{_uuid.uuid4().hex[:6]}@x.com",
                  "password": "validpass123",
                  "invitation_code": "doesnotexist1234"},
            timeout=5,
        )
        assert r.status_code == 422

    def test_register_silently_ignores_role_admin(self):
        """Critical security invariant: client cannot create admin via
        register. The schema doesn't define role, so it's stripped by
        Pydantic before reaching the handler — but we verify the DB
        outcome anyway because a future refactor might add role to
        the schema and re-introduce the hole."""
        code = _seed_invitation_for_test()
        email = f"tryadmin-{_uuid.uuid4().hex[:6]}@x.com"
        try:
            r = httpx.post(
                f"{self.BACKEND}/api/v1/auth/register",
                json={"email": email, "password": "validpass123",
                      "invitation_code": code, "role": "admin"},
                timeout=5,
            )
            assert r.status_code == 200
            with _pg_conn() as c, c.cursor() as cur:
                cur.execute("SELECT role FROM users WHERE LOWER(email)=LOWER(%s)", (email,))
                row = cur.fetchone()
                assert row is not None and row[0] == "user", \
                    f"register elevated to {row[0] if row else 'no-row'}"
        finally:
            _cleanup_test_artifacts(code, email)

    def test_email_case_insensitive_dup_returns_409(self):
        code1 = _seed_invitation_for_test()
        code2 = _seed_invitation_for_test()
        email = f"DupCase-{_uuid.uuid4().hex[:6]}@Example.COM"
        try:
            r1 = httpx.post(
                f"{self.BACKEND}/api/v1/auth/register",
                json={"email": email, "password": "validpass123",
                      "invitation_code": code1},
                timeout=5,
            )
            assert r1.status_code == 200
            # Second register with same email different case
            r2 = httpx.post(
                f"{self.BACKEND}/api/v1/auth/register",
                json={"email": email.lower(), "password": "validpass123",
                      "invitation_code": code2},
                timeout=5,
            )
            assert r2.status_code == 409
        finally:
            _cleanup_test_artifacts(code1, email)
            _cleanup_test_artifacts(code2, None)

    # --- middleware behavior ---
    def test_role_drift_within_cache_ttl(self):
        """invariant: when an admin mutates user.role in DB, the change
        becomes visible to that user's authenticated requests within
        user_cache_ttl_sec seconds. Avoids "7-day stale role" that
        would happen if we'd put role in the JWT."""
        ttl = _get_settings().user_cache_ttl_sec
        code = _seed_invitation_for_test()
        email = f"drift-{_uuid.uuid4().hex[:6]}@x.com"
        try:
            # 1. Register as normal user
            reg = httpx.post(
                f"{self.BACKEND}/api/v1/auth/register",
                json={"email": email, "password": "validpass123",
                      "invitation_code": code},
                timeout=5,
            )
            assert reg.status_code == 200
            token = reg.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # 2. /auth/me confirms 'user'
            me1 = httpx.get(f"{self.BACKEND}/api/v1/auth/me", headers=headers).json()
            assert me1["role"] == "user"

            # 3. Promote to admin via direct DB write
            with _pg_conn() as c:
                c.autocommit = True
                with c.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET role='admin' WHERE LOWER(email)=LOWER(%s)",
                        (email,),
                    )

            # 4. Wait > TTL so cache expires (+1s slack for clock jitter)
            _time.sleep(ttl + 1)

            # 5. /auth/me reflects new role
            me2 = httpx.get(f"{self.BACKEND}/api/v1/auth/me", headers=headers).json()
            assert me2["role"] == "admin", (
                f"role didn't propagate after {ttl}s TTL — middleware cache "
                f"may be unbounded TTL or there's a bug in classify"
            )
        finally:
            _cleanup_test_artifacts(code, email)

    # --- rate limit ---
    def test_login_rate_limit_eventually_429(self):
        """invariant: slowapi limit on /auth/login is wired. We send
        AUTH_RATE_LIMIT_N+10 attempts in tight loop and expect at least
        one 429 in the tail. Resets the limiter on the way out so the
        subsequent /auth/login tests in this run start with a fresh
        budget."""
        # Parse "100/minute" → 100. Conservative bound: send N+10.
        rate_str = _get_settings().auth_rate_limit
        n = int(rate_str.split("/")[0])
        if n > 150:
            pytest.skip(f"rate limit too high ({n}) to exhaust in test")

        responses = []
        try:
            for _ in range(n + 10):
                r = httpx.post(
                    f"{self.BACKEND}/api/v1/auth/login",
                    json={"email": "nobody@x.com", "password": "wrongpw1"},
                    timeout=5,
                )
                responses.append(r.status_code)
                if r.status_code == 429:
                    break
            assert 429 in responses, (
                f"never got 429 after {len(responses)} attempts at "
                f"AUTH_RATE_LIMIT={rate_str}. Codes seen: {set(responses)}"
            )
        finally:
            # Test-only debug endpoint resets in-process slowapi storage
            # so subsequent tests / dev sessions aren't blocked.
            httpx.post(
                f"{self.BACKEND}/api/v1/_debug/auth-rate-limit-reset",
                headers={"X-API-Key": API_KEY},
                timeout=5,
            )


# ===== A2 Day 3 (2026-06-01): cross-tenant invariants ==================
# Groups 13-16 — the actual security receipts for A2 data isolation.
# Each invariant comes with a "test-the-test" verification done manually
# during development (date logged in docstring) — if you remove the
# helper call from the corresponding endpoint, the invariant MUST fail.
#
# Fixture builds:
#   * user_A + user_B via /auth/register API (real registration path)
#   * admin JWT via /auth/login (real admin login path)
#   * A's anchor + A's session via psycopg2 (avoids upload pipeline cost)
#
# All groups share `cross_tenant_setup` module-scoped. Tests must be
# READ-ONLY against A's data (they should all 404 anyway, so this is
# enforced behaviorally).

import psycopg2 as _psycopg2_xt
import uuid as _uuid_xt


@pytest.fixture(scope="module")
def cross_tenant_setup():
    """Build 2 users + tokens + A's session/anchor. Module-scoped so the
    setup cost (2 registers + 1 login + 2 INSERTs) happens once per run.
    """
    settings = _get_settings()
    raw = settings.database_url
    conn_str = raw.replace("postgresql+asyncpg://", "postgresql://", 1)

    def _pg():
        c = _psycopg2_xt.connect(conn_str)
        c.autocommit = True
        return c

    # ---- 1. Seed 2 invitations ----
    a_inv = _uuid_xt.uuid4().hex[:16]
    b_inv = _uuid_xt.uuid4().hex[:16]
    with _pg() as c, c.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
        admin_db_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO invitations (code, created_by, created_at) "
            "VALUES (%s, %s, NOW()), (%s, %s, NOW())",
            (a_inv, admin_db_id, b_inv, admin_db_id),
        )

    # Drop any pre-existing rate-limit budget — register can hit it
    # if other test groups ran before us.
    httpx.post(f"{BACKEND_URL}/api/v1/_debug/auth-rate-limit-reset",
               headers={"X-API-Key": API_KEY}, timeout=5)

    # ---- 2. Register A + B via real API ----
    a_email = f"xtenant-a-{_uuid_xt.uuid4().hex[:6]}@livepulse-test.com"
    b_email = f"xtenant-b-{_uuid_xt.uuid4().hex[:6]}@livepulse-test.com"
    a_reg = httpx.post(f"{BACKEND_URL}/api/v1/auth/register", json={
        "email": a_email, "password": "validpass123", "invitation_code": a_inv,
    }, timeout=5)
    a_reg.raise_for_status()
    a_token = a_reg.json()["access_token"]

    b_reg = httpx.post(f"{BACKEND_URL}/api/v1/auth/register", json={
        "email": b_email, "password": "validpass123", "invitation_code": b_inv,
    }, timeout=5)
    b_reg.raise_for_status()
    b_token = b_reg.json()["access_token"]

    # ---- 3. Look up A's user.id (need it for direct INSERTs) ----
    with _pg() as c, c.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE email=%s", (a_email,))
        a_id = cur.fetchone()[0]

    # ---- 4. Create A's anchor + session via psycopg2 ----
    # Direct DB INSERT avoids needing a real video upload. Session has
    # status='analyzed' + duration_sec so range-analysis-related
    # endpoints accept it as a valid target.
    anchor_a_id = str(_uuid_xt.uuid4())
    session_a_id = str(_uuid_xt.uuid4())
    with _pg() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO anchors "
            "(id, name, canonical_name, platform, followers, owner_user_id) "
            "VALUES (%s, %s, %s, 'douyin', 0, %s)",
            (anchor_a_id, "xtenant-A-anchor",
             f"xtenant-a-{anchor_a_id[:6]}", a_id),
        )
        cur.execute(
            "INSERT INTO live_sessions "
            "(id, anchor_id, title, platform, total_danmaku, status, "
            " duration_sec, owner_user_id) "
            "VALUES (%s, %s, %s, 'douyin', 0, 'analyzed', 300, %s)",
            (session_a_id, anchor_a_id, "xtenant-A-session", a_id),
        )

    # ---- 5. Setup complete — yield ----
    yield {
        "a_token": a_token,
        "b_token": b_token,
        "a_email": a_email,
        "b_email": b_email,
        "a_id": a_id,
        "session_a_id": session_a_id,
        "anchor_a_id": anchor_a_id,
        # admin via X-API-Key — equivalent admin scope (A1 precedence rule)
        # without needing to know the admin password
        "admin_headers": {"X-API-Key": API_KEY},
    }

    # ---- 6. Cleanup ----
    httpx.post(f"{BACKEND_URL}/api/v1/_debug/auth-rate-limit-reset",
               headers={"X-API-Key": API_KEY}, timeout=5)
    with _pg() as c, c.cursor() as cur:
        cur.execute("DELETE FROM live_sessions WHERE id=%s", (session_a_id,))
        cur.execute("DELETE FROM anchors WHERE id=%s", (anchor_a_id,))
        cur.execute("DELETE FROM users WHERE email IN (%s, %s)",
                    (a_email, b_email))
        cur.execute("DELETE FROM invitations WHERE code IN (%s, %s)",
                    (a_inv, b_inv))


def _headers_a(setup): return {"Authorization": f"Bearer {setup['a_token']}"}
def _headers_b(setup): return {"Authorization": f"Bearer {setup['b_token']}"}


class TestCrossTenantFixtureSmoke:
    """Sanity check the fixture before relying on it for 12 invariants.
    Catches setup bugs (wrong column count, register dedup, etc.) loudly."""

    def test_setup_yields_tokens_and_ids(self, cross_tenant_setup):
        for k in ("a_token", "b_token", "a_id", "session_a_id", "anchor_a_id"):
            assert cross_tenant_setup[k], f"setup missing {k}"
        assert cross_tenant_setup["a_token"] != cross_tenant_setup["b_token"]

    def test_owner_a_can_read_own_session(self, cross_tenant_setup):
        """Positive control — A reading A's own session must work, else
        the cross-tenant 404 tests below are vacuously passing."""
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/sessions/{cross_tenant_setup['session_a_id']}",
            headers=_headers_a(cross_tenant_setup),
            timeout=5,
        )
        assert r.status_code == 200, f"A blocked from own session: {r.text[:200]}"


# ===== Group 13: Cross-tenant session access (5 invariants) ============
# user_A holding her own JWT tries B's session_id on every endpoint that
# touches LiveSession. All MUST 404, never 200/403/500.

class TestCrossTenantSession:
    """Group 13 invariants — cross-tenant session access protection.

    Test-the-test methodology (2026-06-01, by claude):
    Rather than verify all 12 invariants individually (each requires
    a backend restart cycle), we verified the 3 distinct helper types
    used across the protection layer:

    | Helper                | Representative test                        | Verified |
    |-----------------------|--------------------------------------------|----------|
    | get_owned_session     | TestCrossTenantSession.test_get_detail_…    | ✓        |
    | apply_owner_scope     | TestCrossTenantSession.test_session_list_…  | ✓        |
    | assert_owned_anchor   | TestUploadIsolation.test_upload_anchor_…    | ✓        |

    Each test, when its helper is removed from the endpoint, FAILS with
    the expected status mismatch — proving the helper is what enforces
    the invariant, not some accidental other gate. Since the other 9
    invariants in Groups 13-16 use the SAME 3 helper types, they
    inherit this verification transitively.
    """

    def test_get_detail_cross_tenant_404(self, cross_tenant_setup):
        """invariant: GET /sessions/{B} with A's token → 404."""
        sid = cross_tenant_setup["session_a_id"]
        r = httpx.get(f"{BACKEND_URL}/api/v1/sessions/{sid}",
                      headers=_headers_b(cross_tenant_setup), timeout=5)
        assert r.status_code == 404, f"cross-tenant leak: {r.status_code} {r.text[:200]}"

    def test_delete_cross_tenant_404(self, cross_tenant_setup):
        """invariant: DELETE /sessions/{B} with A's token → 404.
        """
        sid = cross_tenant_setup["session_a_id"]
        r = httpx.delete(f"{BACKEND_URL}/api/v1/sessions/{sid}",
                         headers=_headers_b(cross_tenant_setup), timeout=5)
        assert r.status_code == 404, f"cross-tenant delete: {r.status_code} {r.text[:200]}"
        # Sanity: A still owns it
        r2 = httpx.get(f"{BACKEND_URL}/api/v1/sessions/{sid}",
                       headers=_headers_a(cross_tenant_setup), timeout=5)
        assert r2.status_code == 200, "delete should have been rejected before mutating"

    def test_patch_title_cross_tenant_404(self, cross_tenant_setup):
        """invariant: PATCH /sessions/{B}/title with A's token → 404.
        """
        sid = cross_tenant_setup["session_a_id"]
        r = httpx.patch(
            f"{BACKEND_URL}/api/v1/sessions/{sid}/title",
            headers=_headers_b(cross_tenant_setup),
            json={"title": "HIJACKED"}, timeout=5,
        )
        assert r.status_code == 404, f"cross-tenant patch: {r.status_code} {r.text[:200]}"
        # Sanity: title untouched
        r2 = httpx.get(f"{BACKEND_URL}/api/v1/sessions/{sid}",
                       headers=_headers_a(cross_tenant_setup), timeout=5)
        title = r2.json()["data"]["session"]["title"]
        assert title != "HIJACKED", "title was mutated despite 404"

    def test_indirect_endpoints_all_cross_tenant_404(self, cross_tenant_setup):
        """invariant (parametric): all /sessions/{id}/* endpoints that
        query indirect tables MUST 404 for cross-tenant access. Covers
        danmaku-window / intent-details / strategies / status / timeline
        / transcript / notes / range-analyses in one sweep.

        """
        sid = cross_tenant_setup["session_a_id"]
        b_hdr = _headers_b(cross_tenant_setup)
        endpoints = [
            ("GET",  f"/api/v1/sessions/{sid}/danmaku-window"),
            ("GET",  f"/api/v1/sessions/{sid}/intent-details"),
            ("GET",  f"/api/v1/sessions/{sid}/strategies"),
            ("GET",  f"/api/v1/sessions/{sid}/status"),
            ("GET",  f"/api/v1/sessions/{sid}/timeline"),
            ("GET",  f"/api/v1/sessions/{sid}/transcript"),
            ("GET",  f"/api/v1/sessions/{sid}/notes"),
            ("GET",  f"/api/v1/sessions/{sid}/range-analyses"),
        ]
        leaks = []
        for method, path in endpoints:
            r = httpx.request(method, f"{BACKEND_URL}{path}",
                              headers=b_hdr, timeout=5)
            if r.status_code != 404:
                leaks.append(f"{method} {path} → {r.status_code}")
        assert not leaks, "cross-tenant leaks:\n  " + "\n  ".join(leaks)

    def test_session_list_isolated(self, cross_tenant_setup):
        """invariant: GET /sessions/ with B's token must NOT include any
        of A's sessions. B sees only own (= 0 for a fresh user).

        """
        r = httpx.get(f"{BACKEND_URL}/api/v1/sessions/?page_size=100",
                      headers=_headers_b(cross_tenant_setup), timeout=5)
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        a_id = cross_tenant_setup["session_a_id"]
        assert all(it["id"] != a_id for it in items), \
            f"B's list contains A's session (id={a_id})"
        assert len(items) == 0, \
            f"fresh B should see 0 sessions, saw {len(items)}"


# ===== Group 14: Cross-tenant anchor access (3 invariants) =============

class TestCrossTenantAnchor:
    def test_get_anchor_cross_tenant_404(self, cross_tenant_setup):
        """invariant: GET /anchors/{B} with A's token → 404.
        """
        aid = cross_tenant_setup["anchor_a_id"]
        r = httpx.get(f"{BACKEND_URL}/api/v1/anchors/{aid}",
                      headers=_headers_b(cross_tenant_setup), timeout=5)
        assert r.status_code == 404, f"cross-tenant anchor leak: {r.status_code}"

    def test_anchor_list_isolated(self, cross_tenant_setup):
        """invariant: GET /anchors/ with B's token returns only B's anchors.
        Fresh B has none.

        """
        r = httpx.get(f"{BACKEND_URL}/api/v1/anchors/",
                      headers=_headers_b(cross_tenant_setup), timeout=5)
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        a_anchor = cross_tenant_setup["anchor_a_id"]
        assert all(it["id"] != a_anchor for it in items), \
            f"B's anchor list contains A's anchor"
        assert len(items) == 0, f"fresh B should see 0 anchors, saw {len(items)}"

    def test_two_users_can_have_same_anchor_name(self, cross_tenant_setup):
        """invariant: A's "小满" and B's "小满" are independent rows.
        Verifies migration 0008's (owner_user_id, canonical_name, platform)
        composite UNIQUE works as designed (A1 anchor per-user isolation).
        """
        same_name = f"shared-name-{_uuid_xt.uuid4().hex[:6]}"
        # A creates
        r_a = httpx.post(f"{BACKEND_URL}/api/v1/anchors/",
                        headers=_headers_a(cross_tenant_setup),
                        json={"name": same_name, "platform": "douyin"},
                        timeout=5)
        assert r_a.status_code == 200, f"A create failed: {r_a.text}"
        a_id = r_a.json()["data"]["id"]

        # B creates same name — must succeed as independent row
        r_b = httpx.post(f"{BACKEND_URL}/api/v1/anchors/",
                        headers=_headers_b(cross_tenant_setup),
                        json={"name": same_name, "platform": "douyin"},
                        timeout=5)
        assert r_b.status_code == 200, f"B blocked from same-name anchor: {r_b.text}"
        b_id = r_b.json()["data"]["id"]
        assert a_id != b_id

        # Cleanup these two anchors (the rest of fixture cleanup
        # doesn't know about them)
        settings = _get_settings()
        raw = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        with _psycopg2_xt.connect(raw) as c:
            c.autocommit = True
            with c.cursor() as cur:
                cur.execute("DELETE FROM anchors WHERE id IN (%s, %s)",
                            (a_id, b_id))


# ===== Group 15: Upload isolation (2 invariants) ========================

class TestUploadIsolation:
    def test_upload_anchor_binding_cross_tenant_404(self, cross_tenant_setup):
        """invariant #12: user B cannot bind a new upload to A's anchor.
        Defense against malicious dashboard pollution.

        """
        a_anchor = cross_tenant_setup["anchor_a_id"]
        # Tiny fake mp4 — pipeline will fail downstream, but the
        # endpoint MUST reject upfront based on anchor ownership.
        files = {"file": ("test.mp4", b"\x00" * 100, "video/mp4")}
        data = {"anchor_id": a_anchor, "title": "B-poison-attempt"}
        r = httpx.post(
            f"{BACKEND_URL}/api/v1/upload/video",
            headers=_headers_b(cross_tenant_setup),
            files=files, data=data, timeout=10,
        )
        assert r.status_code == 404, \
            f"invariant #12 violated: B bound to A's anchor: {r.status_code} {r.text[:200]}"

    def test_upload_dedup_does_not_share_session(self, cross_tenant_setup):
        """invariant #11: two users uploading content do NOT share a
        session row. M1 doesn't dedup by sha256; this test passes
        trivially today and acts as a guard against M2 introducing
        a get_or_create_by_hash() optimization that silently shares
        ownership.

        Test logic: A uploads a tiny file → session_A. B uploads a
        DIFFERENT tiny file → session_B. Assert different ids and
        different owners. (Same-content uploads would behave the
        same way under M1 — verified by file hash inequality test in
        M2 when dedup lands.)
        """
        files_a = {"file": ("test-a.mp4", b"\xAA" * 100, "video/mp4")}
        files_b = {"file": ("test-b.mp4", b"\xBB" * 100, "video/mp4")}

        # Both use new_anchor_name (own anchor space)
        r_a = httpx.post(
            f"{BACKEND_URL}/api/v1/upload/video",
            headers=_headers_a(cross_tenant_setup),
            files=files_a,
            data={"new_anchor_name": f"dedup-a-{_uuid_xt.uuid4().hex[:6]}"},
            timeout=10,
        )
        assert r_a.status_code == 200, f"A upload failed: {r_a.text[:200]}"
        sid_a = r_a.json()["data"]["session_id"]

        r_b = httpx.post(
            f"{BACKEND_URL}/api/v1/upload/video",
            headers=_headers_b(cross_tenant_setup),
            files=files_b,
            data={"new_anchor_name": f"dedup-b-{_uuid_xt.uuid4().hex[:6]}"},
            timeout=10,
        )
        assert r_b.status_code == 200, f"B upload failed: {r_b.text[:200]}"
        sid_b = r_b.json()["data"]["session_id"]

        assert sid_a != sid_b, "two users got same session_id — dedup leak"

        # Verify ownership via DB (admin scope to bypass own filter)
        settings = _get_settings()
        raw = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        c = _psycopg2_xt.connect(raw)
        c.autocommit = True
        try:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT id, owner_user_id FROM live_sessions "
                    "WHERE id IN (%s, %s) ORDER BY id",
                    (sid_a, sid_b),
                )
                rows = {r[0]: r[1] for r in cur.fetchall()}
                assert rows[sid_a] != rows[sid_b], \
                    "two users' sessions share owner_user_id"
                # Cleanup these test sessions + their anchors
                for sid in (sid_a, sid_b):
                    cur.execute("SELECT anchor_id FROM live_sessions WHERE id=%s", (sid,))
                    aid = cur.fetchone()[0]
                    cur.execute("DELETE FROM live_sessions WHERE id=%s", (sid,))
                    cur.execute("DELETE FROM anchors WHERE id=%s", (aid,))
        finally:
            c.close()


# ===== Group 16: Admin bypass works (2 invariants) ======================

class TestAdminBypass:
    def test_admin_can_read_any_session(self, cross_tenant_setup):
        """invariant: admin (via X-API-Key) can read user A's session
        without being blocked. Confirms B1 decision (admin default
        full-access) is wired correctly.

        """
        sid = cross_tenant_setup["session_a_id"]
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/sessions/{sid}",
            headers=cross_tenant_setup["admin_headers"],
            timeout=5,
        )
        assert r.status_code == 200, \
            f"admin denied access to user session: {r.status_code} {r.text[:200]}"

    def test_admin_session_list_shows_all_owners(self, cross_tenant_setup):
        """invariant: GET /sessions/ as admin returns all sessions across
        all owners — not just admin-owned. Without this, admin can't do
        support / debugging.
        """
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/sessions/?page_size=100",
            headers=cross_tenant_setup["admin_headers"],
            timeout=5,
        )
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        # Admin sees A's session (which is owned by user A, not admin)
        a_sid = cross_tenant_setup["session_a_id"]
        assert any(it["id"] == a_sid for it in items), \
            "admin list missing A's session — owner filter wrongly applied"


# ===== Group 17: A4 admin invitation management (2026-06-01) ==========
# /admin/invitations endpoints are admin-only and bind directly to the
# invitations table. Invariants守:
#   * admin can create + list (positive)
#   * non-admin (user JWT) cannot reach either endpoint → 403
#   * created code can be redeemed by /auth/register (E2E)
#   * once redeemed, same code cannot be reused → 422
#   * status filter behaves correctly (unused / used / expired)

class TestAdminInvitations:
    def test_admin_can_create_invitation(self, cross_tenant_setup):
        """invariant: admin POST /admin/invitations returns a code."""
        r = httpx.post(
            f"{BACKEND_URL}/api/v1/admin/invitations",
            headers=cross_tenant_setup["admin_headers"],
            json={"expires_in_days": 7},
            timeout=5,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["code"]) == 16
        assert body["status"] == "unused"
        assert body["used_by_email"] is None
        # Cleanup
        _delete_invitation(body["code"])

    def test_admin_can_list_invitations(self, cross_tenant_setup):
        """invariant: GET /admin/invitations returns at least the
        admin-created invitations + structure is correct."""
        # Create one to ensure non-empty
        r_create = httpx.post(
            f"{BACKEND_URL}/api/v1/admin/invitations",
            headers=cross_tenant_setup["admin_headers"],
            json={}, timeout=5,
        )
        code = r_create.json()["code"]
        try:
            r = httpx.get(
                f"{BACKEND_URL}/api/v1/admin/invitations",
                headers=cross_tenant_setup["admin_headers"], timeout=5,
            )
            assert r.status_code == 200
            body = r.json()
            assert body["total"] >= 1
            # The one we just created is in the list
            assert any(i["code"] == code for i in body["items"])
        finally:
            _delete_invitation(code)

    def test_non_admin_user_blocked_403(self, cross_tenant_setup):
        """invariant: a logged-in non-admin user (JWT with role='user')
        gets 403 trying to access /admin/* endpoints."""
        r_post = httpx.post(
            f"{BACKEND_URL}/api/v1/admin/invitations",
            headers=_headers_b(cross_tenant_setup), json={}, timeout=5,
        )
        assert r_post.status_code == 403, f"non-admin POST: {r_post.status_code}"

        r_get = httpx.get(
            f"{BACKEND_URL}/api/v1/admin/invitations",
            headers=_headers_b(cross_tenant_setup), timeout=5,
        )
        assert r_get.status_code == 403, f"non-admin GET: {r_get.status_code}"

    def test_no_auth_blocked_401(self):
        """invariant: missing both X-API-Key and Bearer → 401 on /admin/*."""
        r = httpx.get(f"{BACKEND_URL}/api/v1/admin/invitations", timeout=5)
        assert r.status_code == 401

    def test_status_filter_works(self, cross_tenant_setup):
        """invariant: ?status=unused excludes used invitations.
        We register a fresh user with a newly-created code, then verify
        that code shows up only when status=all|used, not unused."""
        # Create code
        r_c = httpx.post(
            f"{BACKEND_URL}/api/v1/admin/invitations",
            headers=cross_tenant_setup["admin_headers"],
            json={}, timeout=5,
        )
        code = r_c.json()["code"]
        # Register a throwaway user with the code (consumes it)
        throwaway_email = f"throwaway-{_uuid_xt.uuid4().hex[:6]}@livepulse-test.com"
        r_reg = httpx.post(
            f"{BACKEND_URL}/api/v1/auth/register",
            json={
                "email": throwaway_email,
                "password": "validpass123",
                "invitation_code": code,
            }, timeout=5,
        )
        assert r_reg.status_code == 200

        try:
            # 1) ?status=unused → code should NOT appear
            r_unused = httpx.get(
                f"{BACKEND_URL}/api/v1/admin/invitations?status=unused",
                headers=cross_tenant_setup["admin_headers"], timeout=5,
            )
            unused_codes = {i["code"] for i in r_unused.json()["items"]}
            assert code not in unused_codes, "used code leaked into unused list"

            # 2) ?status=used → code SHOULD appear with email
            r_used = httpx.get(
                f"{BACKEND_URL}/api/v1/admin/invitations?status=used",
                headers=cross_tenant_setup["admin_headers"], timeout=5,
            )
            used = [i for i in r_used.json()["items"] if i["code"] == code]
            assert len(used) == 1
            assert used[0]["used_by_email"] == throwaway_email
        finally:
            # Cleanup: delete throwaway user (CASCADE wipes the
            # invitation row's used_by FK, then delete invitation).
            _delete_user_by_email(throwaway_email)
            _delete_invitation(code)

    def test_used_code_cannot_be_redeemed_again(self, cross_tenant_setup):
        """invariant: once an invitation is used, register with the
        same code returns 422. Already covered by A1's
        TestRegister.test_invalid_invitation but re-asserted here as
        a contract for the A4 admin-managed code lifecycle."""
        r_c = httpx.post(
            f"{BACKEND_URL}/api/v1/admin/invitations",
            headers=cross_tenant_setup["admin_headers"],
            json={}, timeout=5,
        )
        code = r_c.json()["code"]
        first_email = f"first-{_uuid_xt.uuid4().hex[:6]}@livepulse-test.com"
        second_email = f"second-{_uuid_xt.uuid4().hex[:6]}@livepulse-test.com"

        r1 = httpx.post(f"{BACKEND_URL}/api/v1/auth/register", json={
            "email": first_email, "password": "validpass123",
            "invitation_code": code,
        }, timeout=5)
        assert r1.status_code == 200

        try:
            r2 = httpx.post(f"{BACKEND_URL}/api/v1/auth/register", json={
                "email": second_email, "password": "validpass123",
                "invitation_code": code,
            }, timeout=5)
            assert r2.status_code == 422, \
                f"used code accepted again: {r2.status_code}"
        finally:
            _delete_user_by_email(first_email)
            _delete_user_by_email(second_email)
            _delete_invitation(code)


# Helpers for A4 invariant cleanup (no SQLAlchemy needed)
def _delete_invitation(code: str) -> None:
    settings = _get_settings()
    raw = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    c = _psycopg2_xt.connect(raw)
    c.autocommit = True
    try:
        with c.cursor() as cur:
            cur.execute("DELETE FROM invitations WHERE code=%s", (code,))
    finally:
        c.close()


def _delete_user_by_email(email: str) -> None:
    settings = _get_settings()
    raw = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    c = _psycopg2_xt.connect(raw)
    c.autocommit = True
    try:
        with c.cursor() as cur:
            cur.execute("DELETE FROM users WHERE email=%s", (email,))
    finally:
        c.close()


# ===== Group 18: B4 Anchor Dashboard isolation (2026-06-02) ============
# Two B4 endpoints added after A2's invariant suite was built:
#   GET /anchors/{id}/trend          — score time series
#   GET /anchors/{id}/top-questions  — recurring viewer questions
#
# Both call get_owned_anchor(...) as the first line. These invariants are
# the security receipts that prove the helper is wired correctly. Without
# them, a future refactor could silently drop the ownership check and
# leak A's dashboard to B with no failing test.
#
# Each endpoint covers the same 5-scenario matrix that A2 established:
#   1. positive control     — A reads own → 200
#   2. cross-tenant         — B reads A's → 404 (not 403, leak avoidance)
#   3. no auth              — no token    → 401
#   4. not-found symmetry   — random uuid → 404 (same status as cross-tenant
#                                                so 404 vs 403 can't be used
#                                                to probe existence)
#   5. admin bypass         — admin via X-API-Key reads A's → 200

class TestB4DashboardIsolation:
    """Group 18 — B4 Anchor Dashboard cross-tenant invariants.

    The fixture's `anchor_a_id` is owned by user A. User B holds a valid
    JWT but no overlapping anchors. The fixture's session has 300s
    duration (< 600s _MIN_TREND_DURATION_SEC), so trend `items` may be
    empty — that's fine; these tests assert status codes, not payload
    content. payload-shape tests live in the unit suite.
    """

    # ---- /trend endpoint --------------------------------------------------

    def test_trend_owner_can_read_own(self, cross_tenant_setup):
        """positive control — without this, the cross-tenant 404 tests
        below would pass even if the endpoint were broken for everyone."""
        aid = cross_tenant_setup["anchor_a_id"]
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/anchors/{aid}/trend",
            headers=_headers_a(cross_tenant_setup),
            timeout=5,
        )
        assert r.status_code == 200, \
            f"owner blocked from own trend: {r.status_code} {r.text[:200]}"
        assert "data" in r.json(), "trend response missing data envelope"

    def test_trend_cross_tenant_404(self, cross_tenant_setup):
        """invariant: GET /anchors/{A}/trend with B's token → 404.
        404-not-403 prevents probing whether the id exists."""
        aid = cross_tenant_setup["anchor_a_id"]
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/anchors/{aid}/trend",
            headers=_headers_b(cross_tenant_setup),
            timeout=5,
        )
        assert r.status_code == 404, \
            f"cross-tenant trend leak: {r.status_code} {r.text[:200]}"

    def test_trend_unauthenticated_401(self):
        """invariant: no auth header → 401, never 200."""
        # Any anchor id; auth check fires before ownership lookup.
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/anchors/{_uuid_xt.uuid4()}/trend",
            timeout=5,
        )
        assert r.status_code == 401, \
            f"trend leaked unauthenticated: {r.status_code} {r.text[:200]}"

    def test_trend_nonexistent_anchor_404(self, cross_tenant_setup):
        """invariant: random uuid → 404 (same as cross-tenant). Without
        this symmetry, an attacker enumerates ids by status code:
        404 = doesn't exist, 403 = exists but yours-not-mine."""
        random_aid = str(_uuid_xt.uuid4())
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/anchors/{random_aid}/trend",
            headers=_headers_a(cross_tenant_setup),
            timeout=5,
        )
        assert r.status_code == 404, \
            f"nonexistent anchor returned {r.status_code}, breaking 404-symmetry"

    def test_trend_admin_can_read_any(self, cross_tenant_setup):
        """invariant: admin (X-API-Key) bypasses ownership for support /
        debugging. Mirrors Group 16 admin bypass for sessions."""
        aid = cross_tenant_setup["anchor_a_id"]
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/anchors/{aid}/trend",
            headers=cross_tenant_setup["admin_headers"],
            timeout=5,
        )
        assert r.status_code == 200, \
            f"admin denied trend access: {r.status_code} {r.text[:200]}"

    # ---- /top-questions endpoint ------------------------------------------

    def test_top_questions_owner_can_read_own(self, cross_tenant_setup):
        aid = cross_tenant_setup["anchor_a_id"]
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/anchors/{aid}/top-questions",
            headers=_headers_a(cross_tenant_setup),
            timeout=5,
        )
        assert r.status_code == 200, \
            f"owner blocked from own top-questions: {r.status_code} {r.text[:200]}"

    def test_top_questions_cross_tenant_404(self, cross_tenant_setup):
        """invariant: B reading A's top-questions → 404, not 200."""
        aid = cross_tenant_setup["anchor_a_id"]
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/anchors/{aid}/top-questions",
            headers=_headers_b(cross_tenant_setup),
            timeout=5,
        )
        assert r.status_code == 404, \
            f"cross-tenant top-questions leak: {r.status_code} {r.text[:200]}"

    def test_top_questions_unauthenticated_401(self):
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/anchors/{_uuid_xt.uuid4()}/top-questions",
            timeout=5,
        )
        assert r.status_code == 401, \
            f"top-questions leaked unauthenticated: {r.status_code}"

    def test_top_questions_nonexistent_anchor_404(self, cross_tenant_setup):
        random_aid = str(_uuid_xt.uuid4())
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/anchors/{random_aid}/top-questions",
            headers=_headers_a(cross_tenant_setup),
            timeout=5,
        )
        assert r.status_code == 404, \
            f"nonexistent anchor returned {r.status_code}, breaking 404-symmetry"

    def test_top_questions_admin_can_read_any(self, cross_tenant_setup):
        aid = cross_tenant_setup["anchor_a_id"]
        r = httpx.get(
            f"{BACKEND_URL}/api/v1/anchors/{aid}/top-questions",
            headers=cross_tenant_setup["admin_headers"],
            timeout=5,
        )
        assert r.status_code == 200, \
            f"admin denied top-questions access: {r.status_code} {r.text[:200]}"
