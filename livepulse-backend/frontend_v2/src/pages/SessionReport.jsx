import { useState, useMemo, useEffect } from "react";
import { useParams } from "react-router-dom";
import { Spin, Empty } from "antd";
import ReactECharts from "echarts-for-react";
import useSessionData from "../hooks/useSessionData";
import useFunnelAnalysis from "../hooks/useFunnelAnalysis";
import { toSafeString } from "../utils/textDecoder";
import KeywordDensityCard from "../components/KeywordDensityCard";
import {
  StrategyDrawer,
  IntentDanmakuDrawer,
  ConversionEvidenceDrawer,
} from "../components/SessionDrawers";

/**
 * SessionReport — 某场的分析报告（v2 重做）。
 *
 * W3 Day 1：基础架构 + header + CoreProblem + Top3Actions
 * W3 Day 2-5：依次加 Funnel / Triggers / Timeline / Recommendations
 *
 * 状态机：
 *   loading                 → Spinner
 *   error                   → 错误卡
 *   status=processing       → 进度页（W4 完善）
 *   status=analyzed         → 完整报告（Day 1-5 逐步实现）
 */
export default function SessionReport() {
  const { id: sessionId } = useParams();
  const { session, scriptSegments, danmakus, loading, error, historicalComparison, progress } =
    useSessionData(sessionId);
  const analysis = useFunnelAnalysis(session, scriptSegments, danmakus);

  // Drawer 状态：3 个 drawer 各自独立
  const [strategyDrawer, setStrategyDrawer] = useState({ open: false, tactic: null, title: "" });
  const [intentDrawer, setIntentDrawer] = useState({ open: false, intent: null, title: "" });
  const [evidenceDrawer, setEvidenceDrawer] = useState({ open: false, trigger: null });

  if (loading && !session) {
    return (
      <div style={{ padding: 60, textAlign: "center" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error || !session) {
    return (
      <section className="session-view-shot">
        <Empty description={error || "场次不存在或无权限"} />
      </section>
    );
  }

  const status = session.status;
  const isAnalyzed = status === "analyzed" || status === "done";

  // 非 analyzed 状态：详细步骤 checklist + elapsed 计时器 + 早期 Top3
  if (!isAnalyzed) {
    return (
      <section className="session-view">
        <SessionHeader session={session} />
        <ProgressPanel
          progress={progress}
          startedAt={progress?.analysis_started_at}
          status={status}
        />
        {/* B5 早期 Top3：5 分钟内出 — early_analysis step 完成后就有 */}
        {session?.analysis_summary?.early_insights?.length > 0 && (
          <EarlyTop3Card early={session.analysis_summary} />
        )}
      </section>
    );
  }

  return (
    <section className="session-view">
      <SessionHeader session={session} />

      {/* W3 Day 4: 数据完整性警告 */}
      {session.analysis_summary?.data_completeness?.notes?.length > 0 && (
        <CompletenessBanner
          notes={session.analysis_summary.data_completeness.notes}
        />
      )}

      {/* W3 Day 4: 历史对比（如果有 baseline 数据） */}
      {historicalComparison && (
        <HistoricalComparisonCard comparison={historicalComparison} />
      )}

      {/* W3 Day 1: 核心问题 */}
      {analysis?.coreProblem && <CoreProblemCard problem={analysis.coreProblem} />}

      {/* W3 Day 1: 本场 3 个亮点 */}
      {analysis?.top3Actions?.length > 0 && (
        <Top3Card actions={analysis.top3Actions} />
      )}

      {/* W3 Day 2: 漏斗 + 话术策略分布（左右两列） */}
      <div className="report-two-col">
        <div className="report-col-left">
          {analysis?.funnelAnalysis?.length > 0 && (
            <FunnelCard
              funnel={analysis.funnelAnalysis}
              onStageClick={(stage) =>
                setIntentDrawer({
                  open: true,
                  intent: stage.type,
                  title: `${stage.label}弹幕`,
                })
              }
            />
          )}
          {analysis?.tacticDistribution?.length > 0 && (
            <TacticDistribution
              tactics={analysis.tacticDistribution}
              onTacticClick={(t) =>
                setStrategyDrawer({
                  open: true,
                  tactic: t.type,
                  title: `${t.label}话术样本`,
                })
              }
            />
          )}
        </div>
        <div className="report-col-right">
          {analysis?.conversionTriggers?.length > 0 && (
            <TriggersCard
              title="最容易触发购买的话术"
              accent="conversion"
              triggers={analysis.conversionTriggers}
              onTriggerClick={(t) =>
                setEvidenceDrawer({ open: true, trigger: t })
              }
            />
          )}

          {/* W3 Day 3: 观众曲线 */}
          <ViewerCurveCard
            snapshots={session.viewer_snapshots || []}
            conversionTriggers={analysis?.conversionTriggers || []}
          />

          {analysis?.engagementTriggers?.length > 0 && (
            <TriggersCard
              title="最强互动片段"
              accent="engagement"
              triggers={analysis.engagementTriggers}
              onTriggerClick={() =>
                setStrategyDrawer({
                  open: true,
                  tactic: "interactive_hook",
                  title: "互动高峰话术样本",
                })
              }
            />
          )}
        </div>
      </div>

      {/* W3 Day 3: 话术时间轴（全宽，下方接 AI 叙述） */}
      {analysis?.semanticTimeline?.length > 0 && (
        <TimelineCard
          timeline={analysis.semanticTimeline}
          totalDuration={session.duration_sec || 0}
        />
      )}

      {/* W3 Day 4: 话术密度 + 关键词热度 */}
      <KeywordDensityCard
        scriptSegments={scriptSegments}
        durationSec={session.duration_sec}
      />

      {/* W3 Day 4: 优化建议（教练 narrative，放最下方贴近用户视线终点） */}
      <OptimizationSuggestionCard
        recommendations={analysis?.recommendations || []}
      />

      {/* W3 Day 5: 3 个 Drawer */}
      <StrategyDrawer
        open={strategyDrawer.open}
        title={strategyDrawer.title}
        sessionId={sessionId}
        tactic={strategyDrawer.tactic}
        onClose={() => setStrategyDrawer((s) => ({ ...s, open: false }))}
      />
      <IntentDanmakuDrawer
        open={intentDrawer.open}
        title={intentDrawer.title}
        sessionId={sessionId}
        intent={intentDrawer.intent}
        onClose={() => setIntentDrawer((s) => ({ ...s, open: false }))}
      />
      <ConversionEvidenceDrawer
        open={evidenceDrawer.open}
        sessionId={sessionId}
        trigger={evidenceDrawer.trigger}
        onClose={() => setEvidenceDrawer((s) => ({ ...s, open: false }))}
      />
    </section>
  );
}

/* ============================================================
   Components
   ============================================================ */

function SessionHeader({ session }) {
  const score = session.composite_score;
  const date = formatDate(session.start_time || session.created_at);
  const durationMin = session.duration_sec
    ? Math.round(session.duration_sec / 60)
    : null;
  const anchorName = session.anchor_name;

  return (
    <div className="report-header">
      <div>
        <div className="breadcrumb">
          {anchorName && <span>{toSafeString(anchorName)} · </span>}分析报告
        </div>
        <h2 className="session-title">{toSafeString(session.title) || "未命名场次"}</h2>
        <div className="session-meta">
          {date} {durationMin != null && `· ${durationMin} 分钟`} ·
          scoring {session.analysis_summary?.versions?.scoring_version || "v5"}
        </div>
      </div>
      {score != null && (
        <div className={`score-pill ${scoreClass(score)}`}>
          <div className="score-pill-num">{Math.round(score)}</div>
          <div className="score-pill-label">综合分</div>
        </div>
      )}
    </div>
  );
}

function CoreProblemCard({ problem }) {
  const sev = problem.severity || "medium";
  return (
    <div className={`core-problem severity-${sev}`}>
      <div className="core-problem-label">本场核心问题</div>
      <div className="core-problem-text">{toSafeString(problem.problem)}</div>
      <div className="core-problem-stats">
        <span>
          转化窗口 <strong>{problem.convCount}</strong> 个
        </span>
        <span>
          互动窗口 <strong>{problem.engCount}</strong> 个
        </span>
        <span className="emphasis">
          强购买占比 <strong>{problem.strongPct}%</strong>
        </span>
      </div>
    </div>
  );
}

function Top3Card({ actions }) {
  return (
    <div className="top3-wrap">
      <div className="section-label">
        <div className="section-bar" />
        本场 3 个亮点时刻
      </div>
      <div className="top3-list">
        {actions.map((a) => (
          <div key={a.rank} className="top3-item">
            <div className="top3-rank">#{a.rank}</div>
            <div className="top3-body">
              <div className="top3-title">{toSafeString(a.title)}</div>
              <div className="top3-cause-effect">
                <span className="top3-cause">{a.cause}</span>
                <span className="top3-arrow">→</span>
                <span className="top3-effect">
                  {a.effect}
                  {a.sharePct != null && (
                    <span className="top3-share">（占全场 {a.sharePct}%）</span>
                  )}
                </span>
              </div>
              {a.scriptQuote && (
                <div className="top3-quote">"{a.scriptQuote}"</div>
              )}
              <div className="top3-meta">
                <span>影响力 {a.score}</span>
                <span>·</span>
                <span>{formatTimeRange(a.startSec, a.endSec)}</span>
                {a.dmLift && (
                  <>
                    <span>·</span>
                    <span>弹幕 ↑ {a.dmLift}</span>
                  </>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============================================================
   W3 Day 2: Funnel / Tactics / Triggers
   ============================================================ */

function FunnelCard({ funnel, onStageClick }) {
  const maxCount = Math.max(...funnel.map((f) => f.count), 1);
  const total = funnel.reduce((s, f) => s + f.count, 0);

  return (
    <div className="card-block">
      <div className="card-label">购买意向漏斗（点击层查看弹幕）</div>
      <div className="funnel">
        {funnel.map((stage, idx) => {
          const barW = Math.max((stage.count / maxCount) * 100, 2);
          const prevCount = funnel[idx - 1]?.count;
          const rate =
            idx === 0
              ? "100%"
              : prevCount > 0
              ? `${Math.round((stage.count / prevCount) * 100)}%`
              : "—";
          return (
            <div key={stage.type} className="funnel-row-wrap">
              <div
                className="funnel-row funnel-row-clickable"
                title={`${stage.label} · ${stage.count} 条 · 点击查看弹幕`}
                onClick={() => onStageClick && onStageClick(stage)}
              >
                <span className="funnel-label">{stage.label}</span>
                <div className="funnel-bar-track">
                  <div
                    className="funnel-bar-fill"
                    style={{ width: `${barW}%` }}
                  />
                  <span className="funnel-bar-num">{stage.count}</span>
                </div>
                <span className="funnel-rate">
                  {rate}
                  {stage.dropoff > 0 && (
                    <span className="funnel-dropoff"> -{stage.dropoff}%</span>
                  )}
                </span>
              </div>
              {idx < funnel.length - 1 && <div className="funnel-arrow">↓</div>}
            </div>
          );
        })}
      </div>
      <div className="funnel-total">
        <span>总弹幕</span>
        <strong>{total}</strong>
      </div>
    </div>
  );
}

function TacticDistribution({ tactics, onTacticClick }) {
  const max = Math.max(...tactics.map((t) => t.count), 1);
  return (
    <div className="card-block" style={{ marginTop: 16 }}>
      <div className="card-label">话术策略分布（点击查看话术样本）</div>
      <div className="tactic-list">
        {tactics.slice(0, 8).map((t) => (
          <div
            key={t.type}
            className="tactic-row tactic-row-clickable"
            onClick={() => onTacticClick && onTacticClick(t)}
          >
            <span className="tactic-label">{toSafeString(t.label)}</span>
            <div className="tactic-bar-track">
              <div
                className="tactic-bar-fill"
                style={{ width: `${(t.count / max) * 100}%` }}
              />
            </div>
            <span className="tactic-count">{t.count} 次</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function TriggersCard({ title, accent, triggers, onTriggerClick }) {
  return (
    <div className="triggers-wrap">
      <div className="triggers-title">{title}</div>
      <div className="triggers-list">
        {triggers.map((t) =>
          t.summary || t.script ? (
            <div
              key={t.rank}
              className={`trigger-card accent-${accent} ${
                onTriggerClick ? "trigger-card-clickable" : ""
              }`}
              onClick={() => onTriggerClick && onTriggerClick(t)}
            >
              <div className="trigger-head">
                <span className={`trigger-rank accent-${accent}`}>#{t.rank}</span>
                <span className="trigger-summary">
                  {toSafeString(t.summary || t.script)}
                </span>
                <span className={`trigger-score accent-${accent}`}>{t.score}</span>
              </div>
              {t.claim && (
                <div className="trigger-claim">{toSafeString(t.claim)}</div>
              )}
              <div className="trigger-chips">
                {/* 黄金话术（保留旧 frontend 的逻辑） */}
                {accent === "conversion" &&
                  t.score >= 50 &&
                  t.strongPurchaseCount > 0 &&
                  t.closingTactics > 0 && (
                    <span className="chip-gold">🌟 黄金话术</span>
                  )}
                {t.strongPurchaseCount > 0 && (
                  <span className="chip-strong">
                    强购买 {t.strongPurchaseCount}人
                  </span>
                )}
                {t.closingTactics > 0 && (
                  <span className="chip-closing">
                    逼单 {t.closingTactics}次
                  </span>
                )}
                {t.dmCount > 0 && (
                  <span className="chip-engagement">{t.dmCount} 弹幕</span>
                )}
                {t.lift && (
                  <span className="chip-lift">弹幕 ↑ {t.lift}</span>
                )}
              </div>
            </div>
          ) : null
        )}
      </div>
    </div>
  );
}

/* ============================================================
   W3 Day 3: ViewerCurve / SemanticTimeline + AIDescription
   ============================================================ */

function ViewerCurveCard({ snapshots, conversionTriggers }) {
  const hasData = snapshots && snapshots.length > 1;

  const option = useMemo(() => {
    if (!hasData) return null;
    const data = snapshots
      .filter((s) => s.viewer_count != null)
      .map((s) => [s.timestamp_sec, s.viewer_count]);

    // 转化窗口 markArea
    const bands = (conversionTriggers || [])
      .filter((t) => t.timeRange?.start_sec != null && t.timeRange?.end_sec != null)
      .slice(0, 5)
      .map((t, idx) => [
        {
          xAxis: t.timeRange.start_sec,
          itemStyle: {
            color: `rgba(239, 68, 68, ${0.18 - idx * 0.02})`,
          },
          name: `#${t.rank}`,
        },
        { xAxis: t.timeRange.end_sec },
      ]);

    return {
      grid: { left: 36, right: 16, top: 24, bottom: 24 },
      tooltip: {
        trigger: "axis",
        formatter: (params) => {
          const p = params[0];
          return `${formatMmSs(p.value[0])}<br/>观众 ${p.value[1]}`;
        },
      },
      xAxis: {
        type: "value",
        axisLabel: {
          formatter: (v) => formatMmSs(v),
          fontSize: 10,
          color: "#A3A3A3",
        },
        axisLine: { lineStyle: { color: "#EAEAEA" } },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        axisLabel: { fontSize: 10, color: "#A3A3A3" },
        splitLine: { lineStyle: { color: "#F0F0F0" } },
        axisLine: { show: false },
      },
      series: [
        {
          type: "line",
          smooth: true,
          symbol: "circle",
          symbolSize: 4,
          data,
          lineStyle: { color: "#6366F1", width: 2 },
          itemStyle: { color: "#6366F1" },
          areaStyle: {
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(99, 102, 241, 0.18)" },
                { offset: 1, color: "rgba(99, 102, 241, 0)" },
              ],
            },
          },
          markArea: bands.length > 0 ? { silent: true, data: bands } : undefined,
        },
      ],
    };
  }, [snapshots, conversionTriggers, hasData]);

  return (
    <div className="card-block" style={{ marginBottom: 16 }}>
      <div className="card-label">在线观众曲线</div>
      {hasData ? (
        <ReactECharts
          option={option}
          style={{ height: 200, width: "100%" }}
          opts={{ renderer: "svg" }}
        />
      ) : (
        <div className="empty-mini">
          该场未抽到观众快照 · 历史 session 或 OCR 跳过时常见
        </div>
      )}
    </div>
  );
}

function TimelineCard({ timeline, totalDuration }) {
  const [activeIdx, setActiveIdx] = useState(null);
  const bucketSize = 30;
  const bucketCount = Math.ceil(totalDuration / bucketSize);

  // 把 segments 装进 30s 桶，每个桶取主导 intent
  const buckets = useMemo(() => {
    if (!timeline.length || !bucketCount) return [];
    const arr = Array.from({ length: bucketCount }, (_, i) => ({
      idx: i,
      startSec: i * bucketSize,
      endSec: (i + 1) * bucketSize,
      segments: [],
    }));
    timeline.forEach((seg) => {
      const start = Math.floor(seg.startSec / bucketSize);
      const end = Math.min(Math.floor(seg.endSec / bucketSize), bucketCount - 1);
      for (let i = start; i <= end; i++) {
        if (arr[i]) arr[i].segments.push(seg);
      }
    });
    return arr.map((b) => {
      if (!b.segments.length) return { ...b, color: null, count: 0 };
      // 取所有 seg 中最大 score 的 dominantIntent 为代表色
      const top = b.segments.reduce(
        (acc, s) => ((s.score || 0) > (acc.score || 0) ? s : acc),
        b.segments[0]
      );
      return {
        ...b,
        color: top.intentColor,
        count: b.segments.length,
        topSeg: top,
      };
    });
  }, [timeline, bucketCount]);

  const active = activeIdx != null ? buckets[activeIdx] : null;

  return (
    <div className="timeline-wrap">
      <div className="card-label" style={{ marginBottom: 12, marginTop: 28 }}>
        话术时间轴（30 秒桶 · 点击查看 AI 解读）
      </div>
      <div className="timeline-grid">
        {buckets.map((b) => (
          <button
            key={b.idx}
            className={`timeline-bucket ${activeIdx === b.idx ? "active" : ""}`}
            style={{
              background: b.color || "var(--bg-soft)",
              opacity: b.count === 0 ? 0.2 : 1,
            }}
            onClick={() => setActiveIdx(activeIdx === b.idx ? null : b.idx)}
            title={`${formatMmSs(b.startSec)} - ${formatMmSs(b.endSec)} · ${b.count} 段`}
          />
        ))}
      </div>
      <div className="timeline-axis">
        <span>00:00</span>
        <span>{formatMmSs(totalDuration / 2)}</span>
        <span>{formatMmSs(totalDuration)}</span>
      </div>

      {active && active.segments.length > 0 && (
        <AIDescription bucket={active} />
      )}
    </div>
  );
}

function AIDescription({ bucket }) {
  const segments = bucket.segments;
  // 取首个 seg 作为代表，列出该桶所有 intents 和 tactics
  const top = bucket.topSeg || segments[0];
  const intents = [...new Set(segments.map((s) => s.intentLabel).filter(Boolean))];
  const tactics = [
    ...new Set(segments.flatMap((s) => (s.tacticLabels || []).map((t) => t.label))),
  ].filter(Boolean);
  const scriptTypes = [
    ...new Set(segments.map((s) => s.scriptTypeLabel).filter(Boolean)),
  ];

  return (
    <div className="ai-description">
      <div className="ai-time-range">
        🕒 {formatMmSs(bucket.startSec)} - {formatMmSs(bucket.endSec)}
      </div>
      <div className="ai-text">
        该段含 {segments.length} 段话术
        {scriptTypes.length > 0 && (
          <>，主要为 <strong>{scriptTypes.join(" · ")}</strong></>
        )}
        {intents.length > 0 && (
          <>，弹幕反应以 <strong>{intents.slice(0, 3).join("、")}</strong> 为主</>
        )}
        {tactics.length > 0 && (
          <>。识别到话术策略：<strong>{tactics.slice(0, 5).join("、")}</strong></>
        )}
        。
      </div>
      {top?.text && (
        <div className="ai-quote">"{toSafeString(top.text).slice(0, 160)}"</div>
      )}
    </div>
  );
}

/* ============================================================
   W3 Day 4: HistoricalComparison / OptimizationSuggestion / CompletenessBanner
   ============================================================ */

function CompletenessBanner({ notes }) {
  return (
    <div className="completeness-banner">
      <div className="completeness-title">⚠ 数据完整性提示</div>
      {notes.map((n, i) => (
        <div key={i}>{toSafeString(n)}</div>
      ))}
    </div>
  );
}

function HistoricalComparisonCard({ comparison }) {
  // Two states: insufficient / sufficient
  if (comparison.insufficient) {
    return (
      <div className="hist-compare hist-insufficient">
        <div className="hist-label">历史对比</div>
        <div className="hist-text">
          {toSafeString(
            comparison.reason ||
              "该主播近期场次不足 3 场（30 天内），暂无足够 baseline 对比"
          )}
        </div>
      </div>
    );
  }

  const metrics = comparison.metrics || {};
  const order = ["composite", "content", "engagement", "conversion"];
  const labels = {
    composite: "综合分",
    content: "内容分",
    engagement: "互动分",
    conversion: "转化分",
  };
  const items = order
    .map((k) => ({ key: k, label: labels[k], metric: metrics[k] }))
    .filter((x) => x.metric);

  if (items.length === 0) return null;

  return (
    <div className="hist-compare">
      <div className="hist-label">
        本场 vs 近期基线
        {comparison.baseline_label && (
          <span className="hist-baseline">{toSafeString(comparison.baseline_label)}</span>
        )}
      </div>
      <div className="hist-grid">
        {items.map(({ key, label, metric }) => (
          <HistMetric
            key={key}
            label={label}
            metric={metric}
            deemphasize={key === "composite"}
          />
        ))}
      </div>
    </div>
  );
}

function HistMetric({ label, metric, deemphasize }) {
  const dir = metric.direction;
  const color =
    dir === "up"
      ? "var(--score-high)"
      : dir === "down"
      ? "var(--score-low)"
      : "var(--text-mute)";
  const arrow = dir === "up" ? "↑" : dir === "down" ? "↓" : "≈";
  return (
    <div className={`hist-cell ${deemphasize ? "deemph" : ""}`}>
      <div className="hist-cell-label">{label}</div>
      <div className="hist-cell-row">
        <span className="hist-cell-current">{metric.current}</span>
        <span className="hist-cell-baseline">基线 {metric.baseline}</span>
      </div>
      <div className="hist-cell-trend" style={{ color }}>
        <strong>{arrow}</strong> {toSafeString(metric.label)}
      </div>
    </div>
  );
}

function OptimizationSuggestionCard({ recommendations }) {
  const all = (recommendations || []).filter(
    (r) => r && (r.action || r.display_text)
  );
  const items = all.slice(0, 3);

  // 0 条 → 正向兜底
  if (items.length === 0) {
    return (
      <div className="opt-positive">
        <div className="opt-positive-title">本场没有明显短板 👏</div>
        <div className="opt-positive-text">
          这场的互动、转化、节奏都在合理区间。保持当前节奏，继续累积场次，
          在「跨场进步追踪」里看你的成长曲线。
        </div>
      </div>
    );
  }

  return (
    <div className="opt-wrap">
      <div className="opt-header">
        <div className="opt-title">这 {items.length} 个地方，动一动效果会更好</div>
        <div className="opt-subtitle">
          {all.length > items.length
            ? `按优先级展示前 ${items.length} 条 · 完整建议见上方数据`
            : "根据本场数据为你定制 · 按优先级排序"}
        </div>
      </div>
      <div className="opt-list">
        {items.map((rec, i) => (
          <OptItem key={i} rec={rec} />
        ))}
      </div>
    </div>
  );
}

function OptItem({ rec }) {
  const priority = rec.priority || "medium";
  const action = rec.action || rec.display_text || "";
  const reason = rec.reason || "";
  return (
    <div className={`opt-item priority-${priority}`}>
      <div className="opt-item-tags">
        {rec.category && (
          <span className="opt-category">{toSafeString(rec.category)}</span>
        )}
        <span className={`opt-priority priority-${priority}`}>
          {priority === "high" ? "高优先级" : priority === "low" ? "可选改进" : "中等优先级"}
        </span>
      </div>
      <div className="opt-action">
        <span className="opt-action-tag">行动</span>
        {toSafeString(action)}
      </div>
      {reason && (
        <div className="opt-reason">
          <span className="opt-reason-label">依据：</span>
          {toSafeString(reason)}
        </div>
      )}
    </div>
  );
}

/* ============================================================
   ProgressPanel — processing 状态：步骤 checklist + 计时器
   ============================================================ */

/**
 * HourglassIcon — 沙漏动画图标
 * 用于 processing 状态的「等待分析」UI 替换原来的 Ant <Spin>。
 *
 * 视觉：v2 紫色（var(--accent)）极简沙漏轮廓 + 上半部沙逐渐流到下半部，
 * 满后整体翻转 180° 再重来，3s 一周期。
 *
 * 实现：sand 用 SVG <rect> + clipPath，clipPath 的 rect 用 CSS 关键帧推动。
 * size prop 控制外接正方形边长（大号 48 用于主标题，小号 14 用于 checklist）。
 */
function HourglassIcon({ size = 48, className = "" }) {
  return (
    <span
      className={`hourglass ${className}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <svg viewBox="0 0 24 24" width={size} height={size}>
        <defs>
          {/* 上半部沙的可见区域：从满到空 */}
          <clipPath id="hg-top-clip">
            <rect x="0" y="0" width="24" height="24" className="hg-top-mask" />
          </clipPath>
          {/* 下半部沙的可见区域：从空到满 */}
          <clipPath id="hg-bot-clip">
            <rect x="0" y="0" width="24" height="24" className="hg-bot-mask" />
          </clipPath>
        </defs>

        {/* 沙漏框架（上三角 + 下三角 + 上下盖） */}
        <path
          d="M5 3 H19 M5 21 H19 M6 3 L6 6 Q6 8 8 9.5 L11.2 11.6 Q12 12 12 12 Q12 12 12.8 11.6 L16 9.5 Q18 8 18 6 L18 3 M6 21 L6 18 Q6 16 8 14.5 L11.2 12.4 Q12 12 12 12 Q12 12 12.8 12.4 L16 14.5 Q18 16 18 18 L18 21"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* 上半部沙（实心三角，被 clipPath 自上而下逐渐遮掉） */}
        <g clipPath="url(#hg-top-clip)">
          <path
            d="M7 4.2 L17 4.2 Q17 7 15.4 8.4 L12.4 10.8 Q12 11.1 12 11.1 Q12 11.1 11.6 10.8 L8.6 8.4 Q7 7 7 4.2 Z"
            fill="currentColor"
            opacity="0.85"
          />
        </g>

        {/* 下半部沙（实心三角，被 clipPath 自下而上逐渐露出） */}
        <g clipPath="url(#hg-bot-clip)">
          <path
            d="M7 19.8 L17 19.8 Q17 17 15.4 15.6 L12.4 13.2 Q12 12.9 12 12.9 Q12 12.9 11.6 13.2 L8.6 15.6 Q7 17 7 19.8 Z"
            fill="currentColor"
            opacity="0.85"
          />
        </g>

        {/* 中间细颈处的下落沙粒 */}
        <line
          className="hg-stream"
          x1="12" y1="11.4" x2="12" y2="12.6"
          stroke="currentColor"
          strokeWidth="0.9"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}

// 11 步，跟 backend STEP_ORDER 一致
const STEP_ORDER = [
  ["video_processing",  "视频解码"],
  ["asr",               "语音转文字"],
  ["script_analysis",   "话术分析"],
  ["early_analysis",    "早期洞察"],
  ["ocr",               "弹幕识别"],
  ["viewer_count",      "在线人数识别"],
  ["danmaku_analysis",  "弹幕分析"],
  ["correlation",       "关联分析"],
  ["scoring",           "评分"],
  ["insight_engine",    "洞察生成"],
  ["playback_encode",   "视频转码（便于流畅回放）"],
];

function ProgressPanel({ progress, startedAt, status }) {
  // 1 秒 ticker 让 elapsed 自动走（不依赖 5s status 轮询）
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const pct = Math.max(0, Math.min(100, progress?.progress ?? 0));
  const steps = progress?.steps || [];
  const stepByName = Object.fromEntries(steps.map((s) => [s.name, s]));

  // elapsed text
  let elapsedText = null;
  if (startedAt) {
    const start = new Date(startedAt).getTime();
    if (!isNaN(start)) {
      const sec = Math.max(0, Math.floor((now - start) / 1000));
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      elapsedText = m > 0 ? `${m} 分 ${s} 秒` : `${s} 秒`;
    }
  }

  const isError = status === "error" || status === "failed";

  return (
    <div className="progress-panel">
      <div className="progress-bar-wrap">
        <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="progress-summary">
        <HourglassIcon size={48} className="hourglass-hero" />
        <div className="progress-title">
          {isError ? "分析失败 · 等待重试" : `正在分析中... ${Math.round(pct)}%`}
        </div>
        {elapsedText && (
          <div className="progress-elapsed">已运行 {elapsedText}</div>
        )}
      </div>
      <div className="progress-checklist">
        {STEP_ORDER.map(([name, label]) => {
          const s = stepByName[name];
          const st = s?.status;
          let icon, cls, detail;
          if (st === "done") {
            icon = "✓"; cls = "done";
            detail = s.duration_ms ? formatDuration(s.duration_ms) : "";
          } else if (st === "skipped") {
            icon = "⊘"; cls = "skipped"; detail = "已跳过";
          } else if (st === "running") {
            icon = <HourglassIcon size={14} className="hourglass-inline" />;
            cls = "running"; detail = "进行中…";
          } else if (st === "failed" || st === "error") {
            icon = "✗"; cls = "failed"; detail = "失败（等待重试）";
          } else {
            icon = "○"; cls = "queued"; detail = "";
          }
          return (
            <div key={name} className={`progress-row progress-${cls}`}>
              <span className="progress-icon">{icon}</span>
              <span className="progress-label">{label}</span>
              <span className="progress-detail">{detail}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function formatDuration(ms) {
  if (ms == null) return "";
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)} 秒`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m} 分 ${s} 秒`;
}

/* ============================================================
   EarlyTop3Card — B5 早期 Top3（早期 5 分钟出，processing 时就显示）
   ============================================================ */
function EarlyTop3Card({ early }) {
  const items = early?.early_insights || [];
  if (!items.length) return null;
  return (
    <div className="early-top3">
      <div className="early-top3-head">
        <span className="early-top3-badge">早期洞察</span>
        <span className="early-top3-title">
          {toSafeString(early?.early_summary) || "脚本层已分析完成 · 完整分析继续生成中"}
        </span>
      </div>
      <div className="early-top3-list">
        {items.slice(0, 3).map((ins, i) => (
          <div key={i} className={`early-top3-item severity-${ins.severity ?? 0}`}>
            <span className="early-top3-rank">#{i + 1}</span>
            <div className="early-top3-body">
              <div className="early-top3-dim">{toSafeString(ins.dimension)}</div>
              {ins.action_key && (
                <div className="early-top3-action">
                  {toSafeString(ins.action_key)}
                </div>
              )}
            </div>
            {ins.score != null && (
              <span className="early-top3-score">{Math.round(ins.score)}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============================================================
   Utils
   ============================================================ */

function formatDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
      d.getDate()
    ).padStart(2, "0")}`;
  } catch {
    return "";
  }
}

function formatTimeRange(startSec, endSec) {
  if (startSec == null) return "";
  if (endSec == null) return formatMmSs(startSec);
  return `${formatMmSs(startSec)} – ${formatMmSs(endSec)}`;
}

function formatMmSs(sec) {
  if (sec == null || isNaN(sec)) return "00:00";
  const total = Math.max(0, Math.floor(sec));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function scoreClass(score) {
  if (score >= 75) return "high";
  if (score >= 60) return "medium";
  return "low";
}

function statusLabel(s) {
  return (
    {
      uploaded: "待分析",
      processing: "分析中",
      analyzed: "已完成",
      error: "失败",
      failed: "失败",
    }[s] || s
  );
}
