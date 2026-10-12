"""Demo-mode dataset invariants (pure; no DB / MinIO / cloud calls)."""
from types import SimpleNamespace

from app.config import Settings
from app.services.demo_seed import DEMO_DURATION_SEC, build_demo_dataset

ALLOWED_SCRIPT_TYPES = {"interaction", "product_intro", "promotion", "teaching", "chitchat"}
ALLOWED_IMPACT = {"conversion", "engagement", "content"}
ALLOWED_PRIORITY = {"high", "medium", "low"}


def _sessions():
    return build_demo_dataset()["sessions"]


def test_cloud_keys_are_optional_in_settings():
    s = Settings(
        database_url="postgresql+asyncpg://u:p@localhost/x",
        minio_access_key="k", minio_secret_key="s",
    )
    assert s.aliyun_access_key_id == ""
    assert s.demo_mode is False


def test_dataset_is_deterministic_apart_from_dates():
    a, b = build_demo_dataset(), build_demo_dataset()
    assert [s["title"] for s in a["sessions"]] == [s["title"] for s in b["sessions"]]
    assert a["sessions"][0]["viewer_snapshots"] == b["sessions"][0]["viewer_snapshots"]


def test_every_session_references_a_declared_anchor():
    data = build_demo_dataset()
    keys = {a["key"] for a in data["anchors"]}
    assert {s["anchor"] for s in data["sessions"]} <= keys


def test_segments_are_contiguous_and_cover_the_whole_video():
    for s in _sessions():
        segs = s["segments"]
        assert segs[0]["start_sec"] == 0
        assert segs[-1]["end_sec"] == DEMO_DURATION_SEC
        for prev, nxt in zip(segs, segs[1:]):
            assert prev["end_sec"] == nxt["start_sec"]
        assert {g["script_type"] for g in segs} <= ALLOWED_SCRIPT_TYPES


def test_viewer_curve_is_in_range_and_has_a_peak():
    for s in _sessions():
        snaps = s["viewer_snapshots"]
        assert snaps[0][0] == 0 and snaps[-1][0] == DEMO_DURATION_SEC
        counts = [c for _, c, _ in snaps]
        assert min(counts) > 0
        assert s["viewer_peak"] == max(counts)
        likes = [l for _, _, l in snaps]
        assert likes == sorted(likes)  # cumulative


def test_insight_report_has_what_the_ios_app_decodes():
    for s in _sessions():
        rep = s["analysis_summary"]["insight_report"]
        assert rep["summary"]["overall_score"] == float(s["composite_score"])
        assert rep["insights"], "workspace insights tab would be empty"
        for ins in rep["insights"]:
            assert ins["impact"] in ALLOWED_IMPACT
            assert ins["priority"] in ALLOWED_PRIORITY
            tr = ins["time_range"]
            assert 0 <= tr["start_sec"] < tr["end_sec"] <= DEMO_DURATION_SEC


def test_highlights_are_consumed_by_the_real_api_projection():
    from app.api.workspace import _filtered_highlights

    for s in _sessions():
        fake = SimpleNamespace(analysis_summary=s["analysis_summary"])
        items = _filtered_highlights(fake)
        assert items, "every demo session should have at least one shareable highlight"
        assert items == sorted(items, key=lambda h: h["score"], reverse=True)
        for h in items:
            assert 0 <= h["start_sec"] < h["end_sec"] <= DEMO_DURATION_SEC


def test_scores_are_consistent_between_columns_and_summary():
    for s in _sessions():
        sc = s["analysis_summary"]["scores"]
        assert sc["composite"] == float(s["composite_score"])
        assert all(0 <= v <= 100 for v in sc.values())


def test_demo_shows_improvement_across_sessions_of_the_same_anchor():
    by_anchor = {}
    for s in sorted(_sessions(), key=lambda x: x["start_time"]):
        by_anchor.setdefault(s["anchor"], []).append(float(s["composite_score"]))
    assert by_anchor["xiaoman"] == sorted(by_anchor["xiaoman"]), \
        "trend page should show a visible improvement story"


def test_sessions_are_long_enough_for_the_trend_endpoint():
    # anchors/trend only counts sessions >= _MIN_TREND_DURATION_SEC; a shorter demo
    # would leave the trend page empty.
    from app.api.anchors import _MIN_TREND_DURATION_SEC

    for s in _sessions():
        assert s["duration_sec"] >= _MIN_TREND_DURATION_SEC


def test_insight_titles_quote_the_same_time_range_as_the_data():
    for s in _sessions():
        for ins in s["analysis_summary"]["insight_report"]["insights"]:
            tr = ins["time_range"]
            m1, s1, m2, s2 = tr["start_sec"] // 60, tr["start_sec"] % 60, tr["end_sec"] // 60, tr["end_sec"] % 60
            assert ins["text"].startswith(f"{m1}:{s1:02d}-{m2}:{s2:02d} ")
            assert "{t}" not in ins["text"]


def test_intent_funnel_is_consistent_with_the_conversion_score():
    # The report page headlines "转化率低" when strong_purchase.pct < 5; a session
    # with a high conversion score must not trigger that.
    for s in _sessions():
        f = s["analysis_summary"]["semantic"]["intent_funnel"]
        assert abs(sum(v["pct"] for v in f.values()) - 100) < 0.5
        assert f["strong_purchase"]["pct"] >= 5
        assert f["strong_purchase"]["pct"] > 0 and f["strong_purchase"]["count"] > 0
