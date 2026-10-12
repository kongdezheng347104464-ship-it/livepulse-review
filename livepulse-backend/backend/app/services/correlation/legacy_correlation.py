from collections import defaultdict


def compute_script_danmaku_correlation(
    script_segments: list[dict],
    danmaku_records: list[dict],
    window_sec: int = 30,
) -> dict:
    """Compute correlation between script segments and danmaku activity.

    For each script segment, count how many danmakus occurred within
    a time window after the segment ended.

    Args:
        script_segments: List with start_sec, end_sec, script_type fields.
        danmaku_records: List with timestamp_sec, sentiment, intent fields.
        window_sec: Seconds after segment end to count danmakus.

    Returns:
        Dict with correlation summary.
    """
    correlations = []
    script_type_danmaku: dict[str, int] = defaultdict(int)
    script_type_count: dict[str, int] = defaultdict(int)

    for seg in script_segments:
        start = seg["start_sec"]
        end = seg["end_sec"]
        s_type = seg.get("script_type", "chitchat")

        # Use the embedding-derived intent taxonomy. The legacy keyword
        # tag `intent == "purchase_intent"` is no longer produced.
        PURCHASE_INTENTS = {"strong_purchase", "purchase_consideration"}
        count = 0
        purchase_count = 0
        for d in danmaku_records:
            ts = d["timestamp_sec"]
            if end <= ts <= end + window_sec:
                count += 1
                it = d.get("intent_type") or d.get("intent")
                if it in PURCHASE_INTENTS:
                    purchase_count += 1

        script_type_danmaku[s_type] += count
        script_type_count[s_type] += 1

        correlations.append({
            "start_sec": start,
            "end_sec": end,
            "script_type": s_type,
            "text_preview": seg.get("text", "")[:80],
            "danmaku_count": count,
            "purchase_intent_count": purchase_count,
        })

    by_type = {}
    for s_type in script_type_danmaku:
        total = script_type_danmaku[s_type]
        cnt = script_type_count[s_type]
        by_type[s_type] = {
            "total_danmaku": total,
            "segment_count": cnt,
            "avg_danmaku_per_segment": round(total / cnt, 1) if cnt > 0 else 0,
        }

    top_spikes = sorted(correlations, key=lambda x: x["danmaku_count"], reverse=True)[:5]

    return {
        "correlations": correlations,
        "by_script_type": by_type,
        "top_spikes": top_spikes,
    }
