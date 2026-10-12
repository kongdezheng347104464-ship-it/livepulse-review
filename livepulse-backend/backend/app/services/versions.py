"""Version constants for analytical components.

These versions are stamped into every analysis_summary at compute time
so we can detect cross-version comparisons later (e.g., a session scored
under SCORING_VERSION=v3 should not be naively compared to one scored
under v2 — the formulas produce different distributions).

When you change a formula or policy, BUMP the corresponding version.
Otherwise historical comparison silently mixes incompatible numbers and
users see phantom score drifts ("why is my baseline different from
yesterday?").

History:
  scoring_version
    v1  — original ad-hoc (× 3 / × 5 / pain×3); pre-audit
    v2  — first calibration (engagement×30, conversion÷10×4.2)
    v3  — industry-benchmark calibration of sub-scores
          (engagement: pm/2*100; conversion: per_hour/30*100;
           content: teaching%+product%+promotion%*0.7-chitchat%*0.5)
    v4  — composite redefined as weighted average of three sub-scores
          (content 0.4, engagement 0.3, conversion 0.3). v3's composite
          was script_score-based and degraded silently when OCR skipped
          — same number, misleading "综合分" label.
    v5  — engagement_score becomes a blend of normalized
          engaged_audience_rate (unique_speakers / avg_viewer_count)
          and the legacy prompts_per_minute. Blend ratio 0.6 normalized
          + 0.4 absolute — the absolute term anchors the score so a
          5-viewer session where 4 people talk doesn't ride 80% from a
          tiny sample. Falls back to pure v4 engagement when
          normalized_metrics aren't available (legacy sessions without
          viewer_snapshots).
    v5.1 — current; chitchat penalty in content_score softened from
          -0.5 → -0.2. QA 2026-05-30 M0 caught the original coefficient
          producing content=0 on perfectly normal small-streamer sessions
          (美妆边化妆边聊天 = chitchat 68%, teaching 29%). Soft penalty
          lets such sessions score ~15 instead of 0, more accurately
          reflecting that 闲聊 is a valid 陪伴 mode. Pure-teaching
          sessions still score higher (incentive preserved).
          (internal planning item 🅴-22)

  confidence_version
    v1  — current; 0.5*asr + 0.2*ocr + 0.2*embedding + 0.1*step_success

  baseline_policy_version
    2026-05-27  — current; last 5 within 30 days, valid_for_baseline
                  (duration≥10min, segments≥30), confidence≥0.6, exclude self
"""

SCORING_VERSION = "v5.1"
CONFIDENCE_VERSION = "v1"
BASELINE_POLICY_VERSION = "2026-05-27"


def get_versions() -> dict:
    """Return the version-stamp dict for embedding in analysis_summary."""
    return {
        "scoring_version": SCORING_VERSION,
        "confidence_version": CONFIDENCE_VERSION,
        "baseline_policy_version": BASELINE_POLICY_VERSION,
    }
