/**
 * InsightAdapter - transforms backend data into UI-ready structures.
 *
 * Rule: frontend components NEVER use raw backend fields.
 * Everything goes through this adapter first.
 *
 * All text output passes through normalizeText() to ensure
 * clean UTF-8 strings (no \\uXXXX escapes, no double-encoded JSON).
 */

import { normalizeText } from "../utils/textDecoder";

// ---- Intent label mappings ----
const INTENT_LABELS = {
  strong_purchase: "强购买",
  purchase_consideration: "购买考虑",
  comparison: "比价",
  hesitation: "犹豫",
  interaction: "互动",
};

const INTENT_COLORS = {
  strong_purchase: "#D85A30",
  purchase_consideration: "#F5A623",
  comparison: "#534AB7",
  hesitation: "#8E8EA0",
  interaction: "#C4C4C4",
};

const TACTIC_LABELS = {
  price_anchor: "价格锚点",
  scarcity: "稀缺策略",
  urgency: "紧迫感",
  social_proof: "社会证明",
  risk_reversal: "风险逆转",
  authority: "专业权威",
  emotional_trigger: "情感触发",
  interactive_hook: "互动钩子",
};

const SCRIPT_TYPE_LABELS = {
  teaching: "教学讲解",
  product_intro: "产品介绍",
  promotion: "促销转化",
  interaction: "互动环节",
  chitchat: "闲聊",
};

const FUNNEL_ORDER = [
  "interaction",
  "hesitation",
  "comparison",
  "purchase_consideration",
  "strong_purchase",
];

/**
 * Convert any value to a safe renderable string.
 * Prevents "Objects are not valid as a React child" errors.
 * Always normalizes Unicode escapes via normalizeText().
 */
export function toSafeString(val) {
  return normalizeText(val);
}

const PRIORITY_RANK = { high: 0, medium: 1, low: 2 };

function adaptRecommendations(recs) {
  if (!Array.isArray(recs)) return [];
  const out = recs.map((rec) => {
    if (typeof rec === "string") {
      return { display_text: normalizeText(rec), action: normalizeText(rec), reason: "", category: "", priority: "" };
    }
    return {
      display_text: normalizeText(rec.action || rec.reason || rec),
      action: normalizeText(rec.action || ""),
      reason: normalizeText(rec.reason || ""),
      category: normalizeText(rec.category || ""),
      priority: normalizeText(rec.priority || ""),
    };
  });
  // 按 priority 降序排（high 在前）。同优先级保持原有顺序（稳定排序）
  out.sort((a, b) => (PRIORITY_RANK[a.priority] ?? 99) - (PRIORITY_RANK[b.priority] ?? 99));
  return out;
}

// B5 早期 insights adapter — analysis_summary.early_insights 直接 pass-through。
// 后端已经按 severity×impact desc 排好序，前端不做二次决策（保持 backend
// 是 SSOT 的原则）。
export function adaptEarlyInsights(summary) {
  const early = summary?.early_insights;
  if (!Array.isArray(early) || early.length === 0) return null;
  return {
    insights: early.map((ins) => ({
      dimension:    normalizeText(ins.dimension),
      status:       ins.status,
      severity:     ins.severity,
      score:        ins.score,
      actionKey:    ins.action_key,
      thresholdSet: ins.threshold_set,
    })),
    summary:       normalizeText(summary.early_summary || ""),
    thresholdSet:  summary.early_threshold_set,
    weightsVer:    summary.early_weights_version,
  };
}

export function adaptSessionData(session, scriptSegments, danmakus) {
  const summary = session?.analysis_summary || {};
  const semantic = summary.semantic || {};
  const insightReport = summary.insight_report || {};
  const insights = insightReport.insights || [];
  const recs = insightReport.recommendations || [];
  const funnel = semantic.intent_funnel || {};
  const tacticDist = semantic.tactic_distribution || {};

  const coreProblem = adaptCoreProblem(insights, funnel);
  const conversionTriggers = adaptConversionTriggers(insights);
  const engagementTriggers = adaptEngagementTriggers(insights);
  const funnelAnalysis = adaptFunnel(funnel);
  const semanticTimeline = adaptSemanticTimeline(scriptSegments, danmakus);
  const top3Actions = adaptTop3Actions(insights);

  return {
    coreProblem,
    conversionTriggers,
    engagementTriggers,
    funnelAnalysis,
    semanticTimeline,
    top3Actions,
    recommendations: adaptRecommendations(recs),
    tacticDistribution: adaptTacticDist(tacticDist),
    score: session?.composite_score,
    duration: session?.duration_sec,
    status: session?.status,
    title: normalizeText(session?.title),
    startTime: session?.start_time,
  };
}

// B5 子集 v2 (2026-05-30 晚): "本场 3 个亮点时刻"
// 选 score 最高的 conversion/engagement insights（不是"该改"——那是底部
// recommendations 的角色）。组装 "cause → effect → 占全场比例" 分析链。
function adaptTop3Actions(insights) {
  const valid = (insights || [])
    .filter((i) => i.text && i.score != null && i.time_range?.start_sec != null)
    .filter((i) => (i.score || 0) >= 30)  // 弱信号不算亮点
    .sort((a, b) => (b.score || 0) - (a.score || 0));

  // 计算全场总量用于"占比"分析链：
  // 全场购买意图总数 = 所有 conversion insights 的 purchase_intents 之和
  // 全场弹幕峰值总数 = 所有 engagement insights 的 danmaku 之和
  const totalPurchases = (insights || [])
    .filter((i) => i.impact === "conversion")
    .reduce((s, i) => s + (i.evidence?.purchase_intents || 0), 0);
  const totalEngDanmaku = (insights || [])
    .filter((i) => i.impact === "engagement")
    .reduce((s, i) => s + (i.evidence?.danmaku || 0), 0);

  const picked = [];
  const seenImpacts = new Set();
  for (const ins of valid) {
    if (picked.length >= 3) break;
    if (!seenImpacts.has(ins.impact)) {
      picked.push(ins);
      seenImpacts.add(ins.impact);
    }
  }
  for (const ins of valid) {
    if (picked.length >= 3) break;
    if (!picked.includes(ins)) picked.push(ins);
  }

  return picked.map((ins, idx) => {
    const purchases = ins.evidence?.purchase_intents || 0;
    const danmaku = ins.evidence?.danmaku || 0;
    const closing = ins.evidence?.closing_tactics || 0;
    const engagement = ins.evidence?.engagement || 0;
    const dmLift = ins.evidence?.dm_lift;  // 字符串如 "5.2x"
    const startSec = ins.time_range?.start_sec;
    const endSec = ins.time_range?.end_sec;
    const durationSec = (startSec != null && endSec != null) ? Math.max(0, endSec - startSec) : 0;

    // cause —— "主播做了什么"。优先具体行为，闭环时拿原话片段当佐证
    let cause = "";
    if (ins.impact === "conversion") {
      if (closing > 0 && engagement > 0) cause = `${closing} 次逼单 + ${engagement} 次互动引导`;
      else if (closing > 0) cause = `${closing} 次逼单策略`;
      else if (engagement > 0) cause = `${engagement} 次互动引导`;
      else cause = "连续讲解，未做明显逼单";  // closing=0 时诚实表达，不编故事
    } else if (ins.impact === "engagement") {
      cause = engagement > 0 ? `${engagement} 次互动引导` : "话术引发响应";
    } else if (ins.impact === "rhythm") {
      cause = "直播节奏明显变化";
    } else {
      cause = "话术引导";
    }

    // effect —— "用户反应"。带占比 + dm_lift 双重证据
    let effect = "";
    let sharePct = null;
    if (ins.impact === "conversion" && purchases > 0) {
      effect = `触发 ${purchases} 条购买意图`;
      if (totalPurchases > 0) sharePct = Math.round((purchases / totalPurchases) * 100);
    } else if (ins.impact === "engagement" && danmaku > 0) {
      effect = `触发 ${danmaku} 条弹幕`;
      if (totalEngDanmaku > 0) sharePct = Math.round((danmaku / totalEngDanmaku) * 100);
    } else {
      effect = normalizeText(ins.claim || "");
    }

    // 截取原话当佐证（最多 80 字，多句保留前 2 句）
    const scriptQuote = normalizeText(ins.script_text || "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 80);

    return {
      rank: idx + 1,
      title: normalizeText(ins.text),
      impact: ins.impact,
      score: ins.score,
      priority: ins.priority,
      startSec,
      endSec,
      durationSec,
      cause,
      effect,
      sharePct,
      dmLift: dmLift || null,           // "5.2x" / null
      scriptQuote: scriptQuote || null, // 主播原话片段
      evidence: { purchases, danmaku, closing, engagement },
    };
  });
}

function adaptCoreProblem(insights, funnel) {
  const convInsights = insights.filter((i) => i.impact === "conversion");
  const engInsights = insights.filter((i) => i.impact === "engagement");
  const strongPct = funnel.strong_purchase?.pct || 0;
  const hesPct = funnel.hesitation?.pct || 0;

  let problem = "";
  let severity = "medium";
  if (strongPct < 5) {
    problem = "用户停在观望阶段，转化率低";
    severity = "high";
  } else if (hesPct > 30) {
    problem = "用户犹豫比例高，缺乏促单手段";
    severity = "high";
  } else if (convInsights.length === 0) {
    problem = "未检测到明显转化窗口";
    severity = "high";
  } else {
    problem = `有${convInsights.length}个转化窗口，转化效率${strongPct > 15 ? "高" : "一般"}`;
    severity = strongPct > 15 ? "low" : "medium";
  }
  return { problem, severity, convCount: convInsights.length, engCount: engInsights.length, strongPct, hesPct };
}

function adaptConversionTriggers(insights) {
  // Filter by scope=segment — these are the only insights that point to
  // a specific moment in the broadcast (with `script_text` and a real
  // `time_range`). session-scope insights describe whole-session
  // aggregates and have no "原话" to display. Mixing them used to
  // produce empty-quote artifacts in the evidence drawer.
  return insights
    .filter((i) => i.impact === "conversion" && i.scope === "segment")
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .slice(0, 5)
    .map((ins, idx) => ({
      rank: idx + 1,
      // SSOT: script = original transcript from segments, summary = generated title
      script: normalizeText(ins.script_text || ""),
      summary: normalizeText(ins.text),
      claim: normalizeText(ins.claim),
      score: ins.score,
      priority: ins.priority,
      timeRange: ins.time_range,
      evidence: ins.evidence || {},
      funnelShift: ins.evidence?.dm_lift ? `弹幕↑${ins.evidence.dm_lift}` : "",
      strongPurchaseCount: ins.evidence?.purchase_intents || 0,
      closingTactics: ins.evidence?.closing_tactics || 0,
      semanticReason: normalizeText(ins.claim || buildExplanation(ins)),
    }));
}

function adaptEngagementTriggers(insights) {
  // See adaptConversionTriggers — same scope=segment requirement.
  return insights
    .filter((i) => i.impact === "engagement" && i.scope === "segment")
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .slice(0, 5)
    .map((ins, idx) => ({
      rank: idx + 1,
      script: normalizeText(ins.script_text || ""),
      summary: normalizeText(ins.text),
      claim: normalizeText(ins.claim),
      score: ins.score,
      timeRange: ins.time_range,
      evidence: ins.evidence || {},
      dmCount: ins.evidence?.danmaku || 0,
      engCount: ins.evidence?.engagement || 0,
      lift: ins.evidence?.lift || "",
      semanticReason: normalizeText(ins.claim || buildExplanation(ins)),
    }));
}

function adaptFunnel(funnel) {
  const total = FUNNEL_ORDER.reduce((s, t) => s + (funnel[t]?.count || 0), 0) || 1;
  let prevCount = total;
  return FUNNEL_ORDER.map((type, idx) => {
    const count = funnel[type]?.count || 0;
    const pct = funnel[type]?.pct || 0;
    const dropoff = idx > 0 ? (prevCount > 0 ? Math.round((1 - count / prevCount) * 100) : 100) : 0;
    prevCount = count;
    return { type, label: INTENT_LABELS[type], color: INTENT_COLORS[type], count, pct, dropoff };
  });
}

function adaptTacticDist(tacticDist) {
  return Object.entries(tacticDist)
    .sort((a, b) => b[1] - a[1])
    .map(([type, count]) => ({ type, label: TACTIC_LABELS[type] || type, count }));
}

function adaptSemanticTimeline(segments, danmakus) {
  if (!segments.length) return [];
  return segments.map((seg, idx) => {
    const tags = seg.analysis_tags || {};
    const tactics = tags.embedding_tactics || [];
    const tacticNames = tactics.map((t) => t.type || t);
    const segDanmakus = danmakus.filter(
      (d) => d.timestamp_sec >= seg.start_sec && d.timestamp_sec <= seg.end_sec
    );
    const intentCounts = {};
    segDanmakus.forEach((d) => {
      const it = d.intent_type || d.intent || "interaction";
      intentCounts[it] = (intentCounts[it] || 0) + 1;
    });
    const dominantIntent = Object.entries(intentCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "interaction";
    return {
      index: idx, startSec: seg.start_sec, endSec: seg.end_sec,
      text: normalizeText(seg.text), scriptType: seg.script_type, score: seg.score,
      tactics: tacticNames,
      tacticLabels: tactics.map((t) => ({
        type: t.type || t,
        label: TACTIC_LABELS[t.type || t] || (t.type || t),
        strength: t.strength,
      })),
      dominantIntent,
      intentLabel: INTENT_LABELS[dominantIntent] || dominantIntent,
      intentColor: INTENT_COLORS[dominantIntent] || "#C4C4C4",
      scriptTypeLabel: SCRIPT_TYPE_LABELS[seg.script_type] || seg.script_type,
      danmakuCount: segDanmakus.length, intentCounts,
    };
  });
}

function buildExplanation(ins) {
  if (ins.impact === "conversion") {
    const purchases = ins.evidence?.purchase_intents || 0;
    const closing = ins.evidence?.closing_tactics || 0;
    if (purchases > 0 && closing > 0) return `主播通过${closing}次逼单策略触发${purchases}条购买意图`;
    if (purchases > 0) return `触发${purchases}条购买意图`;
  }
  if (ins.impact === "engagement") {
    const dm = ins.evidence?.danmaku || 0;
    const lift = ins.evidence?.lift || "";
    if (dm > 0) return `触发${dm}条弹幕`;
  }
  return normalizeText(ins.claim || ins.text);
}

export { INTENT_LABELS, INTENT_COLORS, TACTIC_LABELS, FUNNEL_ORDER, SCRIPT_TYPE_LABELS };
