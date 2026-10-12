"""Signal Interpretation Engine.

Upgrades raw pattern detection → business-significant narratives.

5 mechanisms:
1. Absolute thresholds — skip noise
2. Significance scoring — 5-dim weighted (not lift-only)
3. Adjacent window merging — combine fragments
4. Diversity ranking — cap per type
5. Narrative compression — windows → stories
"""

# ---- Thresholds ----
MIN_DANMAKU_THRESHOLD = 5       # below this = noise, skip
MIN_INTERACTION_THRESHOLD = 3   # below this = too sparse
MIN_CONVERSION_THRESHOLD = 1    # need at least 1 purchase intent
MERGE_GAP_SEC = 30              # adjacent windows within this gap → merge
MAX_SAME_TYPE = 3               # max insights per impact category
# Bumped 2026-05-29: 2 was forcing the conversion-trigger UI to show
# only #1 and #2, hiding a real #3 that survived the score sort.
# Bumping to 3 matches the conversion-tab card grid which has room for
# 3 cards. Engagement cap rides along symmetrically.

# Significance scoring weights
WEIGHT_VOLUME = 30      # absolute count matters most
WEIGHT_DURATION = 20    # sustained activity > spike
WEIGHT_CONVERSION = 25  # purchase intent > engagement
WEIGHT_UNIQUENESS = 15  # rare signal > frequent noise
WEIGHT_CONSISTENCY = 10 # steady > erratic

# Impact types
IMPACT_TYPES = ["conversion", "engagement", "rhythm", "content"]


def interpret(patterns: dict, total_danmaku: int, duration_sec: float) -> list[dict]:
    """Full interpretation pipeline: filter → merge → score → diversify → narrate.

    Returns list of narrative insight dicts, ready for insight_generator.
    """
    meta = patterns.get("meta", {})
    raw_hot = patterns.get("hot_segments", [])
    raw_conv = patterns.get("conversion_windows", [])
    raw_drops = patterns.get("drop_segments", [])

    narratives = []

    # ---- Step 1: filter by absolute thresholds + merge adjacent ----
    merged_hot = _merge_adjacent(raw_hot)
    merged_conv = _merge_adjacent(raw_conv)

    # ---- Step 2: score each merged window ----
    for seg in merged_hot:
        dm = seg.get("danmaku", 0)
        eng = seg.get("engagement", 0)
        dur = seg.get("end_sec", 0) - seg.get("start_sec", 0)
        purchase = seg.get("purchase", 0)
        closing = seg.get("closing", 0)

        # Absolute threshold gate
        sig = None
        if purchase >= MIN_CONVERSION_THRESHOLD:
            sig = "conversion"
        elif dm >= MIN_DANMAKU_THRESHOLD:
            sig = "engagement"
        elif eng >= MIN_INTERACTION_THRESHOLD:
            sig = "engagement"
        else:
            continue  # below all thresholds

        dm_avg = meta.get("danmaku_avg", 0.1) or 0.1
        eng_avg = meta.get("engagement_avg", 0.1) or 0.1

        score = _significance_score(
            dm=dm, eng=eng, purchase=purchase, closing=closing,
            duration=dur, dm_avg=dm_avg, eng_avg=eng_avg,
            total_dm=total_danmaku, total_dur=duration_sec,
        )

        narratives.append({
            "type": sig,
            "start_sec": seg["start_sec"],
            "end_sec": seg["end_sec"],
            "danmaku": dm,
            "engagement": eng,
            "purchase": purchase,
            "closing": closing,
            "duration_sec": dur,
            "score": score,
            "dm_lift": round(dm / dm_avg, 1) if dm_avg else 0,
            "eng_lift": round(eng / eng_avg, 1) if eng_avg else 0,
        })

    # Add conversion windows that didn't merge into hot segments
    hot_times = {(n["start_sec"], n["end_sec"]) for n in narratives}
    for seg in merged_conv:
        key = (seg["start_sec"], seg["end_sec"])
        if key in hot_times:
            continue
        purchase = seg.get("purchase_intents", 0)
        closing = seg.get("closing_tactics", 0)
        if purchase < MIN_CONVERSION_THRESHOLD:
            continue
        dur = seg["end_sec"] - seg["start_sec"]
        score = _significance_score(
            dm=0, eng=0, purchase=purchase, closing=closing,
            duration=dur, dm_avg=meta.get("danmaku_avg", 0.1) or 0.1,
            eng_avg=meta.get("engagement_avg", 0.1) or 0.1,
            total_dm=total_danmaku, total_dur=duration_sec,
        )
        narratives.append({
            "type": "conversion",
            "start_sec": seg["start_sec"],
            "end_sec": seg["end_sec"],
            "danmaku": 0,
            "engagement": 0,
            "purchase": purchase,
            "closing": closing,
            "duration_sec": dur,
            "score": score,
            "dm_lift": 0,
            "eng_lift": 0,
        })

    # ---- Step 3: diversity cap ----
    narratives = _diversity_filter(narratives)

    # ---- Step 4: sort by score ----
    narratives.sort(key=lambda n: n["score"], reverse=True)

    return narratives


def _merge_adjacent(segments: list[dict]) -> list[dict]:
    """Merge adjacent windows within MERGE_GAP_SEC into one narrative."""
    if len(segments) <= 1:
        return segments

    sorted_segs = sorted(segments, key=lambda s: s["start_sec"])
    merged = [dict(sorted_segs[0])]

    for seg in sorted_segs[1:]:
        prev = merged[-1]
        gap = seg["start_sec"] - prev["end_sec"]

        if gap <= MERGE_GAP_SEC:
            prev["end_sec"] = max(prev["end_sec"], seg["end_sec"])
            for k in ("danmaku", "engagement", "closing", "purchase", "purchase_intents", "closing_tactics"):
                prev[k] = prev.get(k, 0) + seg.get(k, 0)
        else:
            merged.append(dict(seg))

    return merged


def _significance_score(
    dm: int, eng: int, purchase: int, closing: int,
    duration: float, dm_avg: float, eng_avg: float,
    total_dm: int, total_dur: float,
) -> int:
    """Compute business significance score (0-100).

    Each dimension produces an independent 0-100 sub-score; the final
    significance is a weighted average so the inputs and outputs are
    interpretable. Old implementation stacked multiple `min()` caps and
    a magic `/2` divisor that nobody could explain.

    Sub-scores:
      * volume_sub: how much activity happened in this window vs. typical.
        `dm_avg` is danmaku-per-bucket (30s) average for the whole stream;
        a window with 3x average density scores 100.
      * duration_sub: longer sustained windows are more meaningful than
        single-bucket spikes. 120s saturates.
      * conversion_sub: purchase intents and closing tactics carry the
        most business signal. 5 purchases or 10 closings saturates.
      * uniqueness_sub: rare events (this window's danmaku share of the
        whole stream is small) score higher.
    """
    # Volume sub-score: weighted count of activity vs. typical bucket
    bucket_count = max(total_dm, 1)
    typical_dm = max(dm_avg, 0.5)
    typical_eng = max(eng_avg, 0.2)
    vol_ratio = (dm / typical_dm + eng / typical_eng) / 2  # 1.0 = average
    volume_sub = min(vol_ratio / 3.0 * 100, 100)  # 3x average = 100

    # Duration sub-score: sustained activity wins
    duration_sub = min(duration / 120 * 100, 100)

    # Conversion sub-score: purchase + closing tactics drive this
    conv_sub = min((purchase * 20 + closing * 10), 100)

    # Uniqueness sub-score: smaller share of total = higher novelty
    if total_dm > 0:
        share = dm / total_dm
        if share < 0.03:
            uniq_sub = 100
        elif share < 0.10:
            uniq_sub = 60
        else:
            uniq_sub = 20
    else:
        uniq_sub = 0

    # Weighted average
    total_weight = WEIGHT_VOLUME + WEIGHT_DURATION + WEIGHT_CONVERSION + WEIGHT_UNIQUENESS
    weighted = (
        volume_sub * WEIGHT_VOLUME
        + duration_sub * WEIGHT_DURATION
        + conv_sub * WEIGHT_CONVERSION
        + uniq_sub * WEIGHT_UNIQUENESS
    ) / total_weight
    return int(min(weighted, 100))


def _diversity_filter(narratives: list[dict]) -> list[dict]:
    """Cap MAX_SAME_TYPE per impact type, keep highest-scoring."""
    type_counter = {}
    filtered = []
    for n in sorted(narratives, key=lambda n: n["score"], reverse=True):
        t = n["type"]
        cnt = type_counter.get(t, 0)
        if cnt >= MAX_SAME_TYPE:
            continue
        type_counter[t] = cnt + 1
        filtered.append(n)
    return filtered
