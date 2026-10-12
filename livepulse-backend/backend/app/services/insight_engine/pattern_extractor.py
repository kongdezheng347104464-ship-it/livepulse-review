"""Pattern Extractor: raw events → structured patterns.

Identifies:
- hot_segments: high-engagement time windows
- drop_segments: low-engagement zones
- conversion_windows: segments with purchase intent
- engagement_spikes: sudden interaction peaks
"""


def extract_patterns(
    script_segments: list[dict],
    danmakus: list[dict],
    duration_sec: float,
) -> dict:
    """Extract patterns from timeline events.

    Args:
        script_segments: List of analyzed speech segments.
        danmakus: List of danmaku events.
        duration_sec: Total video duration in seconds.

    Returns:
        Dict with hot_segments, drop_segments, conversion_windows, engagement_spikes.
    """
    if not script_segments:
        return _empty_result()

    total = max(len(script_segments), 1)
    total_dm = max(len(danmakus), 1)
    # When no danmaku data is available (OCR skipped / failed), `drop_segments`
    # would otherwise mark almost every bucket as a "low-interaction zone"
    # because the absence of OCR'd danmakus is indistinguishable from real
    # audience silence. Disable drop detection in that case.
    has_danmaku_signal = len(danmakus) > 0

    # ---- Bucketing (30s windows) ----
    bucket_size = 30
    num_buckets = max(int(duration_sec / bucket_size) + 1, 1)

    buckets = [{"time": i * bucket_size, "danmaku": 0, "engagement": 0,
                "closing": 0, "purchase": 0, "score": 0}
               for i in range(num_buckets)]

    # Fill script data
    for seg in script_segments:
        b = min(int(seg.get("start_sec", 0) / bucket_size), num_buckets - 1)
        tags = seg.get("analysis_tags", {})
        if tags.get("has_engagement_prompt"):
            buckets[b]["engagement"] += 1
        if tags.get("has_closing_tactic"):
            buckets[b]["closing"] += 1
        buckets[b]["score"] += float(seg.get("score", 50))

    # Fill danmaku data
    # Note: `intent_type` is the embedding-derived semantic taxonomy
    # (strong_purchase, purchase_consideration, comparison, hesitation,
    # interaction). The legacy keyword tag `intent == "purchase_intent"`
    # is no longer produced — see danmaku_analyzer F-6 fix. Both strong
    # and consideration count as "purchase" for window detection;
    # comparison/hesitation don't (those are upper-funnel signals).
    PURCHASE_INTENTS = {"strong_purchase", "purchase_consideration"}
    for dm in danmakus:
        b = min(int(dm.get("timestamp_sec", 0) / bucket_size), num_buckets - 1)
        buckets[b]["danmaku"] += 1
        it = dm.get("intent_type") or dm.get("intent")
        if it in PURCHASE_INTENTS:
            buckets[b]["purchase"] += 1

    # ---- Identify patterns ----
    dm_values = [b["danmaku"] for b in buckets]
    eng_values = [b["engagement"] for b in buckets]
    closing_values = [b["closing"] for b in buckets]
    purchase_values = [b["purchase"] for b in buckets]

    if not dm_values:
        return _empty_result()

    dm_avg = sum(dm_values) / len(dm_values) if dm_values else 0
    eng_avg = sum(eng_values) / len(eng_values) if eng_values else 0

    # Hot/spike thresholds use the 80th percentile rather than the previous
    # `mean * 1.5` heuristic. In a roughly Gaussian distribution mean*1.5
    # catches 30-40% of buckets, defeating the purpose of "hotspots".
    # 80th percentile catches the top 20% by construction — they're the
    # genuine outliers a broadcaster should pay attention to.
    # The `max(..., floor)` floors keep tiny-stream edge cases sensible:
    # if a 5-minute test session has 1 average danmaku/bucket, we still
    # want at least 2 to qualify as "hot".
    dm_threshold = max(_percentile(dm_values, 80), 2) if dm_values else 2
    eng_threshold = max(_percentile(eng_values, 80), 1) if eng_values else 1

    hot_segments = []
    drop_segments = []
    conversion_windows = []
    engagement_spikes = []

    for i, b in enumerate(buckets):
        t0 = b["time"]
        t1 = min(t0 + bucket_size, duration_sec)

        # Hot segments: high combined activity. QA 2026-05-29 P0-7:
        # when no danmaku signal exists, the engagement-only criterion
        # (b["engagement"] >= 2) produces false "持续互动高峰（弹幕 0
        # 条）" insights — the script triggered an interactive_hook but
        # we have zero audience-side validation that it landed. Gate the
        # whole append on has_danmaku_signal (same principle as
        # drop_segments at line ~114): no audience data ⇒ can't make
        # audience claims.
        if has_danmaku_signal and (b["danmaku"] >= dm_threshold or b["engagement"] >= 2):
            hot_segments.append({
                "start_sec": t0, "end_sec": t1,
                "danmaku": b["danmaku"], "engagement": b["engagement"],
                "closing": b["closing"], "purchase": b["purchase"],
                "score": round(b["score"] / max(b["danmaku"] + b["engagement"], 1), 1),
            })

        # Drop segments: no audience activity. Only meaningful when we
        # actually have danmaku coverage — otherwise zero danmakus is a
        # measurement gap, not real silence.
        if has_danmaku_signal and b["danmaku"] == 0 and b["engagement"] == 0 and b["closing"] == 0:
            drop_segments.append({"start_sec": t0, "end_sec": t1})

        # Conversion windows: closing tactics + purchase intent
        if b["closing"] > 0 and b["purchase"] > 0:
            conversion_windows.append({
                "start_sec": t0, "end_sec": t1,
                "closing_tactics": b["closing"],
                "purchase_intents": b["purchase"],
            })

        # Engagement spikes: sudden increase. Same gate as hot_segments
        # above — without danmaku signal a "spike" is just the script
        # mentioning the audience, not the audience actually engaging.
        if has_danmaku_signal and b["engagement"] >= eng_threshold:
            engagement_spikes.append({
                "start_sec": t0, "end_sec": t1,
                "engagements": b["engagement"],
                "danmaku_at_spike": b["danmaku"],
            })

    return {
        "hot_segments": hot_segments[:10],
        "drop_segments": drop_segments[:5],
        "conversion_windows": conversion_windows[:10],
        "engagement_spikes": engagement_spikes[:10],
        "meta": {
            "bucket_size_sec": bucket_size,
            "danmaku_avg": round(dm_avg, 1),
            "engagement_avg": round(eng_avg, 1),
            "total_danmaku": total_dm,
        },
    }


def _percentile(values: list, p: float) -> float:
    """Linear-interpolation percentile. Pure-Python so no numpy dependency
    for this single call. Caller guarantees non-empty list.
    """
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    rank = (p / 100) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _empty_result():
    return {
        "hot_segments": [], "drop_segments": [],
        "conversion_windows": [], "engagement_spikes": [],
        "meta": {"bucket_size_sec": 30, "danmaku_avg": 0,
                 "engagement_avg": 0, "total_danmaku": 0},
    }
