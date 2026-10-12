"""ROI configuration: relative coordinates → absolute pixels.

Why relative coordinates (not absolute pixels) at the config layer:
  Different recordings come in different resolutions. The same Douyin
  client UI on a 1080p screen vs a 720p screen vs an 4K screen lands
  at the same RELATIVE position (right-upper viewer badge, right
  side interaction list) but at totally different pixel coordinates.
  If we hardcoded pixels we'd silently produce empty OCR crops on
  every non-1920x1080 source.

  Storing fractions in [0, 1] and multiplying by the actual frame
  dimensions (from ffprobe) at crop time keeps the configuration
  portable across resolutions.

Coordinate convention:
  All values are fractions of frame dimensions:
    x1, x2 ∈ [0, 1]  — left edge / right edge, fraction of width
    y1, y2 ∈ [0, 1]  — top edge / bottom edge, fraction of height
  (0, 0) is top-left of the frame; (1, 1) is bottom-right.

Calibration source:
  Initial values are eyeballed from a 2026-05-27 Douyin recording at
  1920×1080. They are the v0 baseline. After the first real OCR run
  on a new clip we should:
    1. Inspect the actual crop image written by frame_cropper.
    2. If the viewer count text or interaction-list row text is
       partially clipped, nudge the bounds by 0.01-0.02 and rerun.
    3. Once stable, document the calibrated values here.

  Do not hardcode UI-version-specific tweaks here — that's the job
  of the per-session source_resolution / future per-platform config.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelativeRoi:
    """A rectangular region expressed as fractions of frame dimensions.

    Invariant: 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1. The constructor
    does not enforce this — frame_cropper validates at crop time so a
    bad config produces a clear error rather than a silently-zero crop.
    """

    x1: float
    y1: float
    x2: float
    y2: float

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Convert to absolute pixel rect: (x, y, w, h) — the form
        ffmpeg crop= filter expects.

        Rounds to int. The few-pixel inaccuracy that rounding may
        introduce is well within the OCR engine's tolerance; the
        viewer count text alone is typically >30 px tall.
        """
        x = int(self.x1 * width)
        y = int(self.y1 * height)
        w = int((self.x2 - self.x1) * width)
        h = int((self.y2 - self.y1) * height)
        # Defensive minimum: ffmpeg refuses w/h < 1, OCR engine refuses
        # images < 5 px on a side. We clamp to a sentinel that will fail
        # cleanly downstream rather than silently produce empty crops.
        if w < 5 or h < 5:
            raise ValueError(
                f"ROI {self} on {width}x{height} produced w={w} h={h}; "
                "either the relative coordinates are wrong or the source "
                "resolution is too small for this region."
            )
        return x, y, w, h


# ----------------------------------------------------------------------
# Reverted to v0 — eyeballed from the original "standard" Douyin
# screenshot user provided 2026-05-27.
#
# The v1.x iterations chased pixel coordinates against an
# UNREPRESENTATIVE recording frame (window not in the standard size,
# UI sometimes on the contributors tab instead of the events tab).
# That whole calibration thread was wasted work.
#
# Going forward we use FULL-FRAME OCR (no ROI cropping) and let the
# downstream regex / source_classifier extract structure from the
# unconstrained text. This decouples us from:
#   * recording window dimensions
#   * browser chrome height
#   * Douyin UI tab state
#   * future UI redesigns within reason
# These constants are kept ONLY as a fallback should we ever want to
# re-enable region cropping (e.g. to reduce per-call OCR cost). They
# are NOT consumed by the current pipeline.
# ----------------------------------------------------------------------

# Top-right "在线观众·N" viewer badge (v0).
VIEWER_REGION = RelativeRoi(
    x1=0.82, y1=0.10,
    x2=0.97, y2=0.17,
)

# Right-hand interaction list (v0).
INTERACTION_REGION = RelativeRoi(
    x1=0.74, y1=0.20,
    x2=0.99, y2=0.92,
)

# Default source resolution assumed when a session has no recorded
# source_resolution (legacy / pre-2026-05-28 sessions). All legacy
# Douyin recordings observed so far are 1920x1080, so this is a safe
# fallback; the per-session field overrides when present.
DEFAULT_SOURCE_WIDTH = 1920
DEFAULT_SOURCE_HEIGHT = 1080


def parse_resolution(text: str | None) -> tuple[int, int]:
    """Parse "WIDTHxHEIGHT" → (w, h), fall back to default on bad input.

    Accepts "1920x1080", "1920X1080", "1920 x 1080" — anything with
    two digits separated by an x in any case with optional whitespace.
    On parse failure, returns the default and logs nothing here (the
    caller logs context); silent fallback is the right behavior because
    we always have a sensible default for Douyin.
    """
    if not text:
        return DEFAULT_SOURCE_WIDTH, DEFAULT_SOURCE_HEIGHT
    cleaned = text.lower().replace(" ", "")
    parts = cleaned.split("x")
    if len(parts) != 2:
        return DEFAULT_SOURCE_WIDTH, DEFAULT_SOURCE_HEIGHT
    try:
        w, h = int(parts[0]), int(parts[1])
        if w < 100 or h < 100:
            return DEFAULT_SOURCE_WIDTH, DEFAULT_SOURCE_HEIGHT
        return w, h
    except ValueError:
        return DEFAULT_SOURCE_WIDTH, DEFAULT_SOURCE_HEIGHT
