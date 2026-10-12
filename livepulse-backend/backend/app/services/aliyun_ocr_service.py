"""Aliyun OCR API service.

Uses the official alibabacloud_ocr_api20210707 SDK + RecognizeBasic API.

Three extractors with different output contracts:
  extract_danmaku       — legacy, flat (timestamp, content). Used by
                          the pre-2026-05-28 floating-danmaku-region
                          pipeline. Kept for backfill compatibility
                          until P0-Evt-6 lands.
  extract_viewer_count  — per-frame (timestamp, viewer_count) from the
                          top-right "在线观众·N" overlay. Regex extracts
                          the number; frames without a recognizable
                          count are skipped (not zero-filled, because
                          a missing reading is different from "0 viewers").
  extract_interactions  — per-line structured events from the right-
                          hand interaction list. Returns records ready
                          for source_classifier post-processing
                          (username + content split, source_type label,
                          raw_ocr_text preserved for audit).
"""
import json
import logging
import re

from app.config import get_settings
from app.services.live_event.source_classifier import classify_line

settings = get_settings()
_logger = logging.getLogger(__name__)


# Matches the viewer-count overlay text. Douyin renders it as
# "在线观众 · 1019" (full-width middle dot, may or may not have spaces).
# Permissive: captures the trailing digit run after either the dot
# OR — if OCR dropped the dot — directly after the label.
# Comma separators are stripped before int() in case OCR returns
# locale-formatted "1,019".
_VIEWER_RE = re.compile(r"在线观众[\s·•・∙]*?([\d,]+)")

# QA 2026-05-30 B1: phone Douyin viewer-count heuristic blacklist.
# Top region of the frame (Y 5-15%) also contains "本场点赞 25000",
# "帮主播充能 3 AA◇ 145", "1880 加热中 05:19" etc. We want only the
# bare viewer-count number — exclude rows that look like these other
# top metrics by checking for these markers in the same row.
_PHONE_VIEWER_EXCLUDE_MARKERS = (
    "AA", "◇", "加热", "挑战", "充能", "本场", "点赞",
    "粉团", "粉丝", "团", "%", "/", "万", "亿",
)

# Cumulative like count regex.
#
# Real 抖音 UI (both desktop web/OBS and 手机) ALWAYS renders the like
# count as `<N><unit>本场点赞` — number+unit IMMEDIATELY before the label,
# e.g. "11.1万本场点赞", "9982本场点赞". The number never comes after.
#
# 2026-06-03 empirical finding: an earlier "label-then-number" variant
# (本场点赞 <N>) caused a false match against the adjacent 小时榜 element
# "本场点赞 100+" — the 100 belongs to the hourly-rank badge, not likes.
# That phantom pattern is removed. We keep only number-before-label.
#
# `\s*` between every character of "本场点赞" handles OCR breaking the
# 4-char label across columns. The intervening-junk safety comes for
# free: `([\d,.]+)\s*([万亿]?)\s*本场点赞` can't bridge non-space chars,
# so "954② 加粉丝团 11.1万本场点赞" correctly matches "11.1万", not "954".
#
# The phone fallback (bare "点赞 X 万") requires a 万/亿 unit and excludes
# the per-user system event "<user>为主播点赞了" via `(?!了)`.
_LIKE_PATTERNS = (
    # PRIMARY: number+unit immediately before 「本场点赞」. The real UI.
    re.compile(r"([\d,]+\.?\d*)\s*([万亿]?)\s*本\s*场\s*点\s*赞"),
    # Phone fallback: bare 「点赞 X万」 (requires unit, excludes "点赞了")
    re.compile(r"点\s*赞\s*[:：]?\s*([\d,]+\.?\d*)\s*([万亿])(?!\s*了)"),
)


def _parse_like_count(raw_num: str, unit: str) -> int | None:
    """Convert OCR match like ('11.1', '万') → 111000.

    Handles:
      - comma-separated digits ("11,100" → 11100)
      - 万 multiplier (× 10^4)
      - 亿 multiplier (× 10^8)
      - bare integer (no unit)
      - fractional (e.g. "11.1万" → 111000)

    Returns None on parse failure (caller should log + skip).
    """
    if not raw_num:
        return None
    try:
        n = float(raw_num.replace(",", ""))
    except ValueError:
        return None
    if unit == "万":
        n *= 10000
    elif unit == "亿":
        n *= 100000000
    if n < 0 or n > 1e12:   # 1 万亿 hard ceiling — anything past is OCR junk
        return None
    return int(n)


def _extract_like_count(texts: list[str]) -> int | None:
    """Scan OCR text lines for the cumulative '本场点赞' number.

    Tries each pattern in order; first non-None parse wins. Returns None
    if no pattern matched OR the matched substring failed to parse into a
    plausible integer.
    """
    if not texts:
        return None
    # Join into one searchable blob so cross-line splits don't break us
    # (e.g. OCR putting "11.1万" on one line and "本场点赞" on the next).
    blob = " ".join(texts)
    for pat in _LIKE_PATTERNS:
        m = pat.search(blob)
        if not m:
            continue
        raw_num = m.group(1)
        unit = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
        n = _parse_like_count(raw_num, unit)
        if n is not None:
            return n
    return None


def _get_ocr_client():
    """Create Aliyun OCR API client."""
    from alibabacloud_ocr_api20210707.client import Client
    from alibabacloud_tea_openapi import models as open_api_models

    config = open_api_models.Config(
        access_key_id=settings.aliyun_access_key_id,
        access_key_secret=settings.aliyun_access_key_secret,
    )
    config.endpoint = "ocr-api.cn-hangzhou.aliyuncs.com"
    config.read_timeout = 15000  # 15s
    config.connect_timeout = 10000  # 10s
    return Client(config)


def _recognize_image_bytes(image_bytes: bytes) -> list[str]:
    """Call Aliyun RecognizeBasic API on raw image bytes.

    Returns list of recognized text strings.
    """
    from alibabacloud_ocr_api20210707 import models as ocr_models
    from alibabacloud_tea_util import models as util_models

    client = _get_ocr_client()
    request = ocr_models.RecognizeBasicRequest(
        body=image_bytes,
    )
    runtime = util_models.RuntimeOptions()

    result = client.recognize_basic_with_options(request, runtime)
    if result.status_code != 200:
        raise RuntimeError(f"OCR API failed: HTTP {result.status_code}")

    # Parse response JSON
    data_str = result.body.data
    data = json.loads(data_str) if isinstance(data_str, str) else data_str
    texts = []

    content = data.get("content", "")
    if content and content.strip():
        texts.append(content.strip())

    # Also try prism_wordsInfo for individual words
    words_info = data.get("prism_wordsInfo", [])
    for wi in words_info:
        word = wi.get("word", "").strip()
        if word:
            texts.append(word)

    return texts


def _recognize_for_viewer_count(image_bytes: bytes) -> tuple[list[str], list[dict]]:
    """Single OCR call returning both text strings AND raw wordsInfo.

    QA 2026-05-30 B1: phone Douyin viewer-count heuristic needs
    bounding boxes (top-right region detection) which the existing
    `_recognize_image_bytes` discards. To avoid doubling OCR cost
    (~0.005 元 per call), this returns BOTH the texts (for the
    existing `_VIEWER_RE` desktop regex path) AND the raw wordsInfo
    (for the phone fallback).
    """
    from alibabacloud_ocr_api20210707 import models as ocr_models
    from alibabacloud_tea_util import models as util_models

    client = _get_ocr_client()
    request = ocr_models.RecognizeBasicRequest(body=image_bytes)
    runtime = util_models.RuntimeOptions()

    result = client.recognize_basic_with_options(request, runtime)
    if result.status_code != 200:
        raise RuntimeError(f"OCR API failed: HTTP {result.status_code}")

    data_str = result.body.data
    data = json.loads(data_str) if isinstance(data_str, str) else data_str

    texts: list[str] = []
    content = data.get("content", "")
    if content and content.strip():
        texts.append(content.strip())

    words_info = data.get("prism_wordsInfo", []) or []
    for wi in words_info:
        word = (wi.get("word") or "").strip()
        if word:
            texts.append(word)

    return texts, words_info


def _extract_viewer_phone_heuristic(
    words_info: list[dict],
    img_width: int,
    img_height: int,
) -> int | None:
    """Phone Douyin viewer-count heuristic from word bounding boxes.

    QA 2026-05-30 M0 调研: phone Douyin shows in-room audience count
    as a bare 2-5 digit number at top-right (immediately after the
    audience avatar bubbles, before the X close button). No "在线观众"
    text label like the desktop browser version, so `_VIEWER_RE`
    silently fails — that was the M0 finding.

    Heuristic:
      - Y ∈ (5%, 15%) of frame height — skip the status bar at 0-5%
        and stay above the rest of the chrome
      - X ≥ 50% of frame width — right half only
      - Token = pure digits, length 2-5, value 10-50000 (plausible
        viewer count range for small-streamer target)
      - Reject if the same Y-row contains a non-viewer marker
        ("AA / ◇ / 加热 / 挑战 / 充能 / 本场 / 点赞 / 粉团" etc.)
      - Take the RIGHTMOST surviving candidate

    Validated against M0 source `97c5f11d` frames (t=60s "1644",
    t=300s "1880"); both extracted correctly while leaving "138" /
    "145" (帮主播充能) and "25000" (本场点赞) untouched.

    Returns the viewer count or None if no plausible candidate.
    """
    if not words_info or not img_width or not img_height:
        return None

    y_min = img_height * 0.05
    y_max = img_height * 0.15
    x_min = img_width * 0.5

    # First pass: group by row (Y-cluster) so we can check row context
    # before declaring a token a viewer count.
    LINE_TOL = 15
    rows: list[tuple[int, list[tuple[int, str]]]] = []  # (y, [(x, word)])
    for wi in words_info:
        word = (wi.get("word") or "").strip()
        if not word:
            continue
        pos = wi.get("pos") or []
        if not pos:
            continue
        y = pos[0].get("y", 0)
        x = pos[0].get("x", 0)
        if y < y_min or y > y_max:
            continue
        placed = False
        for ry, ws in rows:
            if abs(ry - y) <= LINE_TOL:
                ws.append((x, word))
                placed = True
                break
        if not placed:
            rows.append((y, [(x, word)]))

    candidates: list[tuple[int, int]] = []  # (x, count)
    # For each row, find pure-digit candidates and reject those whose
    # IMMEDIATE LEFT NEIGHBORS contain marker words ("3 AA◇ 145" — 145
    # rejected because AA◇ sits 70px to its left). This is more accurate
    # than "any marker in row", which would also kill the valid "1880"
    # in "1880 加热中 05:19" (加热中 is a sibling time-label,
    # not 1880's label).
    LABEL_X_DIST = 250  # how far left to look for label/marker words
    for _, words in rows:
        words_sorted = sorted(words, key=lambda w: w[0])
        for i, (x, word) in enumerate(words_sorted):
            if x < x_min:
                continue
            cleaned = word.replace(",", "")
            if not cleaned.isdigit() or not (2 <= len(cleaned) <= 5):
                continue
            n = int(cleaned)
            if not (10 <= n <= 50000):
                continue
            has_label_left = False
            for lx, lword in words_sorted[:i]:
                if x - lx > LABEL_X_DIST:
                    continue
                if any(m in lword for m in _PHONE_VIEWER_EXCLUDE_MARKERS):
                    has_label_left = True
                    break
            if not has_label_left:
                candidates.append((x, n))

    if not candidates:
        return None
    # Rightmost wins — viewer count sits closer to the X close button
    # than any other top-right metric.
    candidates.sort(key=lambda c: -c[0])
    return candidates[0][1]


def _recognize_image_bytes_lines(image_bytes: bytes) -> list[str]:
    """Like _recognize_image_bytes but preserves LINE structure.

    Returns a list of cleaned text lines in top-to-bottom reading order.
    Used by the interaction-list extractor where each line is a discrete
    event (one chat message / one gift / one system action).

    Implementation priority (changed 2026-05-28 after empirical test):
      1. PRIMARY: cluster `prism_wordsInfo` entries by Y coordinate.
         RecognizeBasic returns per-word bounding boxes, and grouping
         them by Y gives us the actual visual rows on the interaction
         list, which is exactly what we need.
      2. FALLBACK: split `content` field on '\\n'. This only works
         when Aliyun's layout analysis happened to insert newlines,
         which it does NOT do consistently on the dense interaction
         list (all 8 chat events came back as a single space-joined
         string in 2026-05-28 testing, with no '\\n' separators).

    Why the priority flipped: the `content` field strings of a tall
    interaction-list crop are often the FULL block joined with spaces,
    not newlines. Trusting it would collapse 8+ events into one
    unparseable "line". wordsInfo carries the spatial structure that
    survives layout-analysis collapse.
    """
    from alibabacloud_ocr_api20210707 import models as ocr_models
    from alibabacloud_tea_util import models as util_models

    client = _get_ocr_client()
    request = ocr_models.RecognizeBasicRequest(body=image_bytes)
    runtime = util_models.RuntimeOptions()

    result = client.recognize_basic_with_options(request, runtime)
    if result.status_code != 200:
        raise RuntimeError(f"OCR API failed: HTTP {result.status_code}")

    data_str = result.body.data
    data = json.loads(data_str) if isinstance(data_str, str) else data_str

    # PRIMARY: spatial reconstruction from per-word bounding boxes.
    # Each prism_wordsInfo entry has a `pos` of 4 corner points; row
    # affinity = same top-Y within tolerance. Tolerance set to 15px
    # which is well below the per-row vertical pitch on the Douyin
    # interaction list (~50-60px) and above the within-row jitter
    # (~5px), so it's a safe partition boundary.
    words_info = data.get("prism_wordsInfo", []) or []
    if words_info:
        rows: list[tuple[int, list[tuple[int, str]]]] = []  # (row_y, [(x, word)])
        LINE_TOLERANCE_PX = 15
        for wi in words_info:
            word = (wi.get("word") or "").strip()
            if not word:
                continue
            pos = wi.get("pos") or []
            if not pos:
                continue
            top_y = pos[0].get("y", 0)
            top_x = pos[0].get("x", 0)
            placed = False
            for row_y, words in rows:
                if abs(row_y - top_y) <= LINE_TOLERANCE_PX:
                    words.append((top_x, word))
                    placed = True
                    break
            if not placed:
                rows.append((top_y, [(top_x, word)]))

        rows.sort(key=lambda r: r[0])
        # Join words within a row with a single space; without it,
        # adjacent words run together and break later regex matching.
        # Strip the result because clusters at row edges can leave
        # leading/trailing space.
        lines = [
            " ".join(w for _, w in sorted(row_words, key=lambda x: x[0])).strip()
            for _, row_words in rows
        ]
        # Drop empty rows that survived stripping
        lines = [l for l in lines if l]
        if lines:
            return lines

    # FALLBACK: content split. Only reached when wordsInfo was empty —
    # e.g. on a viewer-count-only crop where the engine returned just
    # the high-level recognized text without per-word boxes.
    content = data.get("content", "") or ""
    if content.strip():
        return [line.strip() for line in content.split("\n") if line.strip()]

    return []


def extract_viewer_count(frame_paths: list, interval_sec: int = 30) -> list[dict]:
    """Read the top-right "在线观众·N" overlay from each frame.

    Returns:
        List of {"timestamp_sec": int, "viewer_count": int}. Frames where
        the regex couldn't extract a number are SKIPPED entirely (not
        added with value 0) — a missing reading is genuinely different
        from "0 viewers" in any analytical query.

    Why a flat per-frame list (vs aggregated avg up here): the analytical
    layer needs the raw series for rolling avg, spike detection, growth
    rate, etc. Aggregating here would prematurely throw away data.
    """
    if settings.aliyun_frozen:
        raise RuntimeError("阿里云 API 已冻结（欠费），请联系管理员充值后解冻")
    if not settings.aliyun_access_key_id:
        raise RuntimeError("Aliyun credentials not configured")

    records: list[dict] = []

    for idx, frame_path in enumerate(frame_paths):
        frame_path_str = str(frame_path)
        timestamp = idx * interval_sec

        try:
            with open(frame_path_str, "rb") as f:
                img_data = f.read()
        except OSError as e:
            _logger.warning("[viewer_count] frame %s unreadable: %s", frame_path_str, e)
            continue

        if len(img_data) < 100:
            continue

        try:
            # QA 2026-05-30 B1: single OCR call returns both texts and
            # wordsInfo so we can try desktop _VIEWER_RE first, then the
            # phone heuristic on the same response (no extra OCR cost).
            texts, words_info = _recognize_for_viewer_count(img_data)
        except Exception as e:
            # OCR per-frame failure is non-fatal — we'd rather have a
            # series with a few gaps than abort the whole step. The log
            # line lets us audit failure rate in pipeline.log.
            _logger.warning(
                "[viewer_count] OCR failed on frame %d (%s): %s",
                idx, frame_path_str, e,
            )
            continue

        # Primary path: desktop browser overlay "在线观众 N" regex.
        matched_count: int | None = None
        for txt in texts:
            m = _VIEWER_RE.search(txt)
            if m:
                try:
                    matched_count = int(m.group(1).replace(",", ""))
                except ValueError:
                    continue
                break

        # Fallback path: phone Douyin (no "在线观众" label, bare number
        # at top-right). Only invoked when desktop regex fails.
        if matched_count is None and words_info:
            try:
                from PIL import Image
                from io import BytesIO
                img = Image.open(BytesIO(img_data))
                w, h = img.size
                matched_count = _extract_viewer_phone_heuristic(words_info, w, h)
                if matched_count is not None:
                    _logger.debug(
                        "[viewer_count] frame %d phone-heuristic matched %d",
                        idx, matched_count,
                    )
            except Exception as e:
                # QA 2026-05-30: previously _logger.debug,which silently
                # swallowed Pillow ImportError(导致 24/24 帧全 0 命中
                # 我们都不知道)。升 warning 让任何启发式失败都立刻可见。
                _logger.warning(
                    "[viewer_count] frame %d phone heuristic error: %s",
                    idx, e,
                )

        # Always also try to extract the cumulative 本场点赞 from the
        # same OCR output — independently of whether viewer_count was
        # found, because some 抖音 web OBS layouts hide "在线观众" but
        # still show the avatar's "11.1万本场点赞" badge.
        like_count = _extract_like_count(texts)

        # Skip the frame only when BOTH signals are missing. A row with
        # one stat is still valuable; gap (= no row) means OCR gave us
        # literally nothing useful from this frame.
        if matched_count is None and like_count is None:
            continue

        records.append({
            "timestamp_sec": timestamp,
            "viewer_count": matched_count,    # may be None
            "like_count": like_count,         # may be None
        })

    return records


def _recognize_all_raw(image_bytes: bytes) -> dict:
    """Single OCR API call returning the raw `data` dict.

    2026-06-03 Day 1: merge of _step_ocr + _step_viewer_count. Both used
    to call recognize_basic_with_options independently — 2× cost on
    every frame. This helper exposes the raw response so downstream
    extractors (lines / viewer / like) can all parse from one call.
    """
    from alibabacloud_ocr_api20210707 import models as ocr_models
    from alibabacloud_tea_util import models as util_models

    client = _get_ocr_client()
    request = ocr_models.RecognizeBasicRequest(body=image_bytes)
    runtime = util_models.RuntimeOptions()

    result = client.recognize_basic_with_options(request, runtime)
    if result.status_code != 200:
        raise RuntimeError(f"OCR API failed: HTTP {result.status_code}")

    data_str = result.body.data
    return json.loads(data_str) if isinstance(data_str, str) else data_str


def _parse_lines_from_data(data: dict) -> list[str]:
    """Reconstruct visual rows from raw OCR data — extracted from
    _recognize_image_bytes_lines so multiple callers can share one
    API response. See that function's docstring for the spatial
    clustering rationale (15px Y tolerance + intra-row X sort).
    """
    words_info = data.get("prism_wordsInfo", []) or []
    if words_info:
        LINE_TOLERANCE_PX = 15
        rows: list[tuple[int, list[tuple[int, str]]]] = []
        for wi in words_info:
            word = (wi.get("word") or "").strip()
            if not word:
                continue
            pos = wi.get("pos") or []
            if not pos:
                continue
            top_y = pos[0].get("y", 0)
            top_x = pos[0].get("x", 0)
            placed = False
            for row_y, words in rows:
                if abs(row_y - top_y) <= LINE_TOLERANCE_PX:
                    words.append((top_x, word))
                    placed = True
                    break
            if not placed:
                rows.append((top_y, [(top_x, word)]))
        rows.sort(key=lambda r: r[0])
        lines = [
            " ".join(w for _, w in sorted(row_words, key=lambda x: x[0])).strip()
            for _, row_words in rows
        ]
        lines = [l for l in lines if l]
        if lines:
            return lines

    content = data.get("content", "") or ""
    if content.strip():
        return [line.strip() for line in content.split("\n") if line.strip()]
    return []


def _parse_texts_from_data(data: dict) -> list[str]:
    """Flat text list from raw OCR data — for _VIEWER_RE / _extract_like_count
    callers that don't need row structure. Mirrors the output of the legacy
    _recognize_image_bytes (content + per-word strings)."""
    texts: list[str] = []
    content = data.get("content", "")
    if content and content.strip():
        texts.append(content.strip())
    words_info = data.get("prism_wordsInfo", []) or []
    for wi in words_info:
        word = (wi.get("word") or "").strip()
        if word:
            texts.append(word)
    return texts


def _parse_viewer_like_from_data(
    data: dict, img_width: int | None, img_height: int | None
) -> tuple[int | None, int | None]:
    """Extract viewer_count + like_count from one OCR raw response.

    Mirrors extract_viewer_count's logic (desktop _VIEWER_RE first,
    then phone heuristic) but takes pre-fetched data dict rather than
    image bytes — used by extract_all_per_frame to avoid OCR duplication.
    """
    texts = _parse_texts_from_data(data)
    words_info = data.get("prism_wordsInfo", []) or []

    # Primary: desktop "在线观众 N" regex
    matched_count: int | None = None
    for txt in texts:
        m = _VIEWER_RE.search(txt)
        if m:
            try:
                matched_count = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            break

    # Fallback: phone heuristic (needs bounding boxes + img dims)
    if matched_count is None and words_info and img_width and img_height:
        try:
            matched_count = _extract_viewer_phone_heuristic(
                words_info, img_width, img_height
            )
        except Exception as e:
            _logger.warning("[viewer_like] phone heuristic error: %s", e)

    # Like count (independent of viewer signal)
    like_count = _extract_like_count(texts)
    return matched_count, like_count


_OCR_DEFAULT_CONCURRENCY = 8


def _process_one_frame(idx: int, frame_path, interval_sec: int) -> dict | None:
    """OCR a single frame and return the raw parse bundle.

    Runs inside a ThreadPoolExecutor worker (extract_all_per_frame).
    Returns None when the frame is unusable so the caller can skip it.
    Network/parse errors are caught + logged so one bad frame never
    fails the whole batch.
    """
    frame_path_str = str(frame_path)
    timestamp = idx * interval_sec

    try:
        with open(frame_path_str, "rb") as f:
            img_data = f.read()
    except OSError as e:
        _logger.warning("[ocr_all] frame %s unreadable: %s", frame_path_str, e)
        return None
    if len(img_data) < 100:
        return None

    try:
        data = _recognize_all_raw(img_data)
    except Exception as e:
        _logger.warning(
            "[ocr_all] OCR failed on frame %d (%s): %s",
            idx, frame_path_str, e,
        )
        return None

    # Dimensions for phone-viewer fallback
    img_w: int | None = None
    img_h: int | None = None
    try:
        from PIL import Image
        from io import BytesIO
        im = Image.open(BytesIO(img_data))
        img_w, img_h = im.size
    except Exception as e:
        _logger.debug("[ocr_all] frame %d no dims (%s) — phone fallback disabled", idx, e)

    # Parse both streams from one response
    lines: list[str] = []
    try:
        lines = _parse_lines_from_data(data)
    except Exception as e:
        _logger.warning("[ocr_all][interactions] parse error frame %d: %s", idx, e)

    viewer: int | None = None
    like: int | None = None
    try:
        viewer, like = _parse_viewer_like_from_data(data, img_w, img_h)
    except Exception as e:
        _logger.warning("[ocr_all][viewer] parse error frame %d: %s", idx, e)

    return {
        "idx": idx,
        "timestamp_sec": timestamp,
        "lines": lines,
        "viewer": viewer,
        "like": like,
    }


def extract_all_per_frame(
    frame_paths: list,
    interval_sec: int = 30,
    max_concurrency: int = _OCR_DEFAULT_CONCURRENCY,
) -> dict:
    """Single-OCR-call combined extractor — concurrent (Day 2, 2026-06-03).

    Day 1 merged 2 OCR calls/frame into 1 (saves cost + cuts a whole
    pipeline step).
    Day 2 fans the per-frame OCR out across a ThreadPoolExecutor with
    `max_concurrency` workers — OCR latency is IO-bound (waiting on
    Aliyun's response, ~200-500ms each), so concurrency scales nearly
    linearly until we hit the API's QPS ceiling. 8 is a conservative
    default that stays well under the typical 10-20 QPS Aliyun grants
    out of the box; tune via env if you've requested a higher quota.

    Returns:
      {
        "interactions": [{timestamp_sec, username, content,
                          source_type, raw_ocr_text}, ...],
        "viewer":       [{timestamp_sec, viewer_count, like_count}, ...],
      }

    Both lists keep the same cross-frame semantics as Day 1:
      - interactions: dedup by (username, content, source_type),
        FIRST-SEEN-frame timestamp wins. Dedup is applied AFTER OCR
        completes so the iteration order is deterministic (by idx),
        not by which worker happened to finish first.
      - viewer: gap-honest. Frames where both viewer_count AND
        like_count parsed as None are dropped, not zero-filled.

    Failure model unchanged: per-frame errors → that frame dropped from
    both outputs, logged at WARNING. A single-frame failure never blocks
    the others (worker threads are independent).

    Test invariant: with max_concurrency=1, output MUST be byte-equal
    to the Day 1 sequential implementation.
    """
    if settings.aliyun_frozen:
        raise RuntimeError("阿里云 API 已冻结（欠费），请联系管理员充值后解冻")
    if not settings.aliyun_access_key_id:
        raise RuntimeError("Aliyun credentials not configured")
    if not frame_paths:
        return {"interactions": [], "viewer": []}

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time

    # Phase 1: fan out OCR calls (parallel)
    per_frame: list[dict | None] = [None] * len(frame_paths)
    t_start = _time.monotonic()
    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        future_to_idx = {
            executor.submit(_process_one_frame, idx, fp, interval_sec): idx
            for idx, fp in enumerate(frame_paths)
        }
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                per_frame[idx] = fut.result()
            except Exception as e:
                _logger.warning("[ocr_all] worker crashed on frame %d: %s", idx, e)
                per_frame[idx] = None
    elapsed = _time.monotonic() - t_start
    _logger.info(
        "[ocr_all] %d frames processed in %.1fs (concurrency=%d, avg=%.2fs/frame)",
        len(frame_paths), elapsed, max_concurrency,
        elapsed / max(1, len(frame_paths)),
    )

    # Phase 2: sequential dedup + assembly (deterministic by idx order)
    interactions: list[dict] = []
    viewer_records: list[dict] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for r in per_frame:
        if r is None:
            continue
        timestamp = r["timestamp_sec"]

        # interactions
        for raw in r["lines"]:
            try:
                ev = classify_line(raw)
            except Exception as e:
                _logger.warning(
                    "[ocr_all][interactions] classify_line crash frame %d: %s",
                    r["idx"], e,
                )
                continue
            key = (ev.username or "", ev.content, ev.source_type.value)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            interactions.append({
                "timestamp_sec": timestamp,
                "username": ev.username,
                "content": ev.content,
                "source_type": ev.source_type.value,
                "raw_ocr_text": ev.raw_ocr_text,
            })

        # viewer / like
        if r["viewer"] is not None or r["like"] is not None:
            viewer_records.append({
                "timestamp_sec": timestamp,
                "viewer_count": r["viewer"],
                "like_count": r["like"],
            })

    return {"interactions": interactions, "viewer": viewer_records}


def extract_interactions(frame_paths: list, interval_sec: int = 30) -> list[dict]:
    """Read structured event lines from the right-side interaction list.

    For each frame, recognize text line-by-line, then run each line
    through source_classifier to assign source_type (speech/gift/
    system/unknown) + extract username + extract content body.

    Cross-frame deduplication is essential because the interaction list
    keeps showing the most recent ~10 events for several minutes — the
    same chat message appears in 6+ consecutive 30s-spaced frames. We
    use (username, content, source_type) as the dedup key and keep
    only the FIRST occurrence (so the recorded timestamp_sec is when
    the event actually appeared in the list, not when it scrolled out).

    Returns:
        List of {timestamp_sec, username, content, source_type,
        raw_ocr_text}. Records are in encounter order (= first-seen
        frame order).

    Failure modes (all non-fatal; logged not raised):
      * OCR transient failure on one frame → that frame's records skipped.
      * source_classifier UNKNOWN on a line → still recorded, with
        source_type='unknown' — surface to operator for rule extension.
    """
    if settings.aliyun_frozen:
        raise RuntimeError("阿里云 API 已冻结（欠费），请联系管理员充值后解冻")
    if not settings.aliyun_access_key_id:
        raise RuntimeError("Aliyun credentials not configured")

    records: list[dict] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for idx, frame_path in enumerate(frame_paths):
        frame_path_str = str(frame_path)
        timestamp = idx * interval_sec

        try:
            with open(frame_path_str, "rb") as f:
                img_data = f.read()
        except OSError as e:
            _logger.warning("[interactions] frame %s unreadable: %s", frame_path_str, e)
            continue

        if len(img_data) < 100:
            continue

        try:
            lines = _recognize_image_bytes_lines(img_data)
        except Exception as e:
            _logger.warning(
                "[interactions] OCR failed on frame %d (%s): %s",
                idx, frame_path_str, e,
            )
            continue

        for raw in lines:
            ev = classify_line(raw)
            # Dedup key includes source_type so that an OCR misread that
            # toggles a line between (e.g.) speech and unknown across
            # frames doesn't suppress the corrected version.
            key = (ev.username or "", ev.content, ev.source_type.value)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            records.append({
                "timestamp_sec": timestamp,
                "username": ev.username,
                "content": ev.content,
                "source_type": ev.source_type.value,
                "raw_ocr_text": ev.raw_ocr_text,
            })

    return records


def extract_danmaku(frame_paths: list, interval_sec: int = 30) -> list[dict]:
    """Extract danmaku text from cropped frame images using Aliyun OCR API.

    Args:
        frame_paths: List of Paths to cropped danmaku region frames.
        interval_sec: Seconds between consecutive frames.

    Returns:
        List of dicts with keys: timestamp_sec, content.
        Adjacent duplicates are merged.
    """
    if settings.aliyun_frozen:
        raise RuntimeError("阿里云 API 已冻结（欠费），请联系管理员充值后解冻")
    if not settings.aliyun_access_key_id:
        raise RuntimeError("Aliyun credentials not configured")

    records: list[dict] = []
    last_text_set: set[str] = set()

    for idx, frame_path in enumerate(frame_paths):
        frame_path = str(frame_path)
        timestamp = idx * interval_sec

        # Read image bytes
        with open(frame_path, "rb") as f:
            img_data = f.read()

        # Skip empty or tiny images
        if len(img_data) < 100:
            continue

        texts = _recognize_image_bytes(img_data)

        # Deduplicate (danmaku persists across frames)
        current_set = set(texts)
        new_texts = current_set - last_text_set
        for text in sorted(new_texts):
            records.append({
                "timestamp_sec": timestamp,
                "content": text,
            })
        last_text_set = current_set

    return records
