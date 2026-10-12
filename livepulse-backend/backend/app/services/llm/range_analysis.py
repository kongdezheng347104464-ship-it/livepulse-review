"""LLM-driven range analysis for selected ScriptSegments.

Architecture (decided 2026-05-29):
  Single provider (Claude) shipped as a flat module, NOT a registry of
  adapters. We intentionally avoid premature abstraction — there is no
  ProviderRegistry, no ProviderFactory, no per-call `provider="..."`
  parameter. When (if) we add a second provider, the protocol below
  becomes the contract and the second module implements it. Until then
  the abstraction stays minimal and the code path is obvious.

Prompt versioning:
  PROMPT_VERSION is stamped on every result row. Bump it ANY time you
  change SYSTEM_PROMPT or the user-message scaffold. The version lets
  future maintenance (rerun against new prompt, A/B compare versions)
  know which rows are stale.

Output contract:
  Claude is asked to emit JSON with exactly these keys:
    { summary: str, keep: [str], remove: [str], optimize: [str] }
  We do NOT use Claude's tool-use feature (overkill for a single
  blocking call) — instead the system prompt mandates JSON-only output
  and we parse the response text. Malformed JSON falls back to a
  best-effort wrapper so the user sees Claude's raw text rather than
  a 500.

Tokens:
  Returned in the result tuple for storage. We don't enforce caps in
  this module — the caller (API endpoint) validates segment count.
  Typical run: ~800-2500 tokens combined.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from anthropic import AsyncAnthropic, APIStatusError

from app.config import get_settings


_logger = logging.getLogger(__name__)

# Bump this string whenever SYSTEM_PROMPT or the user-message scaffold
# below changes. Stored on every SessionAnalysis row.
# v2 (2026-06-03): intent-adaptive redesign — infer streamer type first,
# evaluate against THAT type's goals (never push teaching streamers to
# sell), and output strategic findings (observation→why→action) instead
# of pasted-back transcript fragments. Schema changed keep/remove/optimize
# → streamer_type/highlight/findings[].
PROMPT_VERSION = "v2"

# System prompt — opinionated, in Chinese to match the transcript
# language and the user-facing product. Mandates JSON output.
_SYSTEM_PROMPT = """你是直播复盘教练，专注美妆类目。你的产出是给主播【下一场】用的可执行诊断，不是总结他说了什么。直播是一次性事件，建议永远面向下一场。

## 第一步：先判断主播类型与这段意图
从主播名、标题、话术内容推断主播类型（这一步决定你用什么标准评估）：
- 教学型：以教化妆技巧为主、靠口碑涨粉，几乎不带货（如"教化妆的阿野""小鱼儿教化妆"）
- 带货型：以卖货转化为核心
- 互动陪伴型：以陪伴、聊天、黏性为主
- 混合型：教学中穿插带货
**判断后，必须按【该主播自己的目标】评估。**

## 铁律
- 若主播是教学型/不带货：**禁止**建议他带货、逼单、接产品。教学型的目标是——教得清楚、可跟随、涨粉、留存、口碑。把带货标准套到教学主播头上是错误建议。
- **不要逐句改写话术措辞**——那是文案润色，不是复盘。
- 你的任务是诊断这段在【该主播目标】下的策略问题，给下一场的具体动作。
- 每个问题必须锚定一个时刻（用输入里出现过的时间戳）。

## 评估维度（按主播类型取用）
- 通用（所有类型）：表达清晰度、节奏、互动捕获（痛点人群有没有用"扣1/扣关键词"圈出来）、信任/专业度
- 教学型专属：教学结构是否清晰可跟随、有没有留人/涨粉/进粉丝群的钩子
- 带货型专属（仅当主播确实在卖货时）：转化、产品桥接、逼单时机、痛点→产品

## 输入会包含
1. 场次背景（主播、时长、综合分）
2. 上下文话术（被分析段前后 30s，仅供你判断节奏语境，不要直接评价上下文）
3. 用户重点想分析的话术段

## 输出
只输出一个合法 JSON 对象，禁止 markdown 代码块、解释、前后文字。
JSON schema：
{
  "streamer_type": "教学型｜带货型｜互动陪伴型｜混合型（你的判断）",
  "summary": "一句话整体判断（这段的核心问题或亮点，40 字内）",
  "highlight": "这段最该保留继续做的一个做法，含为什么有效（≤60 字）；确实没有就空字符串",
  "findings": [
    {
      "dimension": "维度（如 教学清晰度/互动/节奏/信任/转化，按主播类型选用）",
      "moment": "mm:ss（问题发生的时刻，必须来自输入话术里的时间戳）",
      "observation": "客观发生了什么，可引用关键原话（≤50 字）",
      "problem": "为什么这是问题（按主播目标的逻辑，不是文字是否通顺）（≤60 字）",
      "action": "下一场具体怎么做（可执行动作，不是改写这句话）（≤80 字）"
    }
  ]
}

要求：
- findings 1-5 条，按重要性排序；确实没问题就空数组，不编造
- 每条 action 必须是"下次怎么做"的动作，不能只说"这里不好"
- 用中文；严禁 markdown，严禁 JSON 外加任何文字"""


@dataclass
class AnalysisSegment:
    """One segment passed into the prompt. start_sec is rendered as
    mm:ss in the prompt for readability."""
    start_sec: int
    end_sec: int
    text: str


@dataclass
class AnalysisContext:
    """Session-level context passed to Claude so it understands the
    broader environment of the selected segments."""
    title: str
    duration_min: int
    composite_score: float | None
    anchor_name: str | None


@dataclass
class AnalysisResult:
    """Parsed Claude response + token accounting."""
    result: dict          # { summary, keep, remove, optimize }
    token_usage: int      # input + output combined
    prompt_version: str   # = PROMPT_VERSION at call time


# ---- Errors ----------------------------------------------------------

class LLMNotConfigured(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is empty. The API endpoint maps
    this to a 503 with a clear message so the user knows what's wrong."""


class LLMCallFailed(RuntimeError):
    """Wraps anthropic SDK errors so the API layer can return 502/500
    without exposing raw exception types to the frontend."""


# ---- Public entrypoint -----------------------------------------------

async def analyze_range(
    selected: list[AnalysisSegment],
    context_before: list[AnalysisSegment],
    context_after: list[AnalysisSegment],
    session: AnalysisContext,
) -> AnalysisResult:
    """Run the range analysis via the configured LLM provider.

    Raises:
      LLMNotConfigured: if the active provider's API key is unset.
      LLMCallFailed:    any SDK or network failure.
    """
    settings = get_settings()
    user_message = _build_user_message(
        selected=selected,
        context_before=context_before,
        context_after=context_after,
        session=session,
    )

    # QA 2026-05-30 G: dispatch on LLM_PROVIDER. claude path preserved
    # for parity / future fallback. JSON output schema is identical so
    # downstream _parse_claude_json works for both providers.
    provider = (settings.llm_provider or "qwen").lower()
    if provider == "qwen":
        return await _call_qwen(settings, user_message)
    return await _call_claude(settings, user_message)


async def _call_claude(settings, user_message: str) -> AnalysisResult:
    """AsyncAnthropic backend (original path)."""
    if not settings.anthropic_api_key:
        raise LLMNotConfigured(
            "ANTHROPIC_API_KEY 未配置，请在 backend/.env 中设置后重启后端"
        )
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        resp = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except APIStatusError as e:
        _logger.warning("Anthropic API error %s: %s", e.status_code, e.message)
        raise LLMCallFailed(f"Claude API 错误 ({e.status_code}): {e.message}") from e
    except Exception as e:
        _logger.exception("Anthropic call crashed: %s", e)
        raise LLMCallFailed(f"Claude 调用失败: {e}") from e

    text = ""
    if resp.content:
        block = resp.content[0]
        text = getattr(block, "text", "") or ""

    parsed = _parse_claude_json(text)
    usage = resp.usage
    token_total = (
        (getattr(usage, "input_tokens", 0) or 0) +
        (getattr(usage, "output_tokens", 0) or 0)
    )
    return AnalysisResult(
        result=parsed,
        token_usage=token_total,
        prompt_version=PROMPT_VERSION,
    )


async def _call_qwen(settings, user_message: str) -> AnalysisResult:
    """DashScope OpenAI-compatible backend (Qwen Max).

    Uses openai SDK pointed at DashScope's compatibility endpoint —
    same chat.completions.create() shape. We force JSON via system
    prompt (same as Claude path) and parse via _parse_claude_json
    which is provider-agnostic.
    """
    if not settings.dashscope_api_key:
        raise LLMNotConfigured(
            "DASHSCOPE_API_KEY 未配置，请在 backend/.env 中设置后重启后端"
        )

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
    )
    try:
        resp = await client.chat.completions.create(
            model=settings.qwen_model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            # response_format JSON mode supported by DashScope as of 2025
            response_format={"type": "json_object"},
        )
    except Exception as e:
        _logger.exception("DashScope/Qwen call crashed: %s", e)
        raise LLMCallFailed(f"Qwen 调用失败: {e}") from e

    text = ""
    if resp.choices:
        text = resp.choices[0].message.content or ""

    parsed = _parse_claude_json(text)
    usage = resp.usage
    token_total = (
        (getattr(usage, "prompt_tokens", 0) or 0) +
        (getattr(usage, "completion_tokens", 0) or 0)
    )
    return AnalysisResult(
        result=parsed,
        token_usage=token_total,
        prompt_version=PROMPT_VERSION,
    )


# ---- Internals -------------------------------------------------------

def _fmt_time(sec: int) -> str:
    """mm:ss formatter for the prompt. Bare seconds confuse the model
    on long sessions ("1840 秒处" vs "30:40")."""
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _render_segments(segs: list[AnalysisSegment]) -> str:
    """Render segments as one line each: '[mm:ss] text'. Newlines
    inside transcript text are not expected (ASR usually strips them)
    but we strip just in case to keep one segment per line."""
    if not segs:
        return "（无）"
    lines = []
    for s in segs:
        cleaned = (s.text or "").replace("\n", " ").strip()
        lines.append(f"[{_fmt_time(s.start_sec)}] {cleaned}")
    return "\n".join(lines)


def _build_user_message(
    *,
    selected: list[AnalysisSegment],
    context_before: list[AnalysisSegment],
    context_after: list[AnalysisSegment],
    session: AnalysisContext,
) -> str:
    """Assemble the user message Claude receives. Keep the structure
    explicit so prompt versioning is meaningful (any change here →
    bump PROMPT_VERSION)."""
    score_str = (
        f"{session.composite_score}" if session.composite_score is not None else "—"
    )
    anchor = session.anchor_name or "—"
    return f"""【场次背景】
- 主播: {anchor}
- 时长: {session.duration_min} 分钟
- 标题: {session.title or '—'}
- 综合分: {score_str}

【上下文（仅参考，不要直接评价）— 选段之前 30s】
{_render_segments(context_before)}

【上下文（仅参考，不要直接评价）— 选段之后 30s】
{_render_segments(context_after)}

【需要重点分析的话术段（共 {len(selected)} 段）】
{_render_segments(selected)}

请按 system prompt 中的 JSON schema 输出分析结果。"""


# Permissive JSON extractor — Claude usually obeys "JSON only" but
# occasionally wraps in ```json ... ``` fences. Strip those and try
# the first {...} block found.
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_claude_json(text: str) -> dict:
    """Best-effort parse of Claude's response into our schema dict.

    Strategy:
      1. Try json.loads on the raw text (the happy path when Claude
         obeyed the system prompt).
      2. Strip ```json ... ``` fences and retry.
      3. Find the first {...} block via regex and parse that.
      4. Give up — return a degraded result with the raw text in
         summary so the user sees what Claude actually said, rather
         than a generic "parse error".

    A degraded result is preferable to an exception: the user has
    already paid for the API call; we shouldn't throw away the output.
    """
    text = text.strip()

    # Strategy 1
    try:
        return _coerce_schema(json.loads(text))
    except (ValueError, TypeError):
        pass

    # Strategy 2: strip markdown fences
    if text.startswith("```"):
        # ```json or ``` then the body then ```
        inner = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        inner = re.sub(r"\n?```$", "", inner).strip()
        try:
            return _coerce_schema(json.loads(inner))
        except (ValueError, TypeError):
            pass

    # Strategy 3: regex first {...}
    m = _JSON_BLOCK_RE.search(text)
    if m:
        try:
            return _coerce_schema(json.loads(m.group(0)))
        except (ValueError, TypeError):
            pass

    # Strategy 4: degraded
    _logger.warning("model returned non-JSON; falling back to raw-text result")
    return {
        "streamer_type": "",
        "summary": "模型返回未能解析为 JSON，下方为原始输出。",
        "highlight": "",
        "findings": [],
        "_raw": text[:2000],  # cap to avoid huge JSONB rows on bad days
    }


def _coerce_schema(data) -> dict:
    """Normalize the parsed dict into the v2 schema with correct types.

    v2 (2026-06-03): streamer_type / summary / highlight / findings[].
    Legacy v1 keys (keep/remove/optimize) are preserved untouched if a
    response still emits them — old SessionAnalysis rows render via the
    frontend's legacy path. Extra keys preserved (forward-compat).
    """
    if not isinstance(data, dict):
        raise ValueError("expected object")
    out = dict(data)
    out["summary"] = str(out.get("summary") or "")
    out["streamer_type"] = str(out.get("streamer_type") or "")
    out["highlight"] = str(out.get("highlight") or "")

    findings = out.get("findings")
    clean = []
    if isinstance(findings, list):
        for f in findings:
            if not isinstance(f, dict):
                continue
            item = {
                "dimension": str(f.get("dimension") or "").strip(),
                "moment": str(f.get("moment") or "").strip(),
                "observation": str(f.get("observation") or "").strip(),
                "problem": str(f.get("problem") or "").strip(),
                "action": str(f.get("action") or "").strip(),
            }
            # Keep a finding only if it carries real content.
            if item["observation"] or item["action"] or item["problem"]:
                clean.append(item)
    out["findings"] = clean

    # Legacy v1 arrays: coerce only if present (don't fabricate empty ones).
    for k in ("keep", "remove", "optimize"):
        v = out.get(k)
        if isinstance(v, list):
            out[k] = [str(x).strip() for x in v if str(x).strip()]
    return out
