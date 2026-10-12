import { useEffect, useState, useMemo } from "react";
import { useParams } from "react-router-dom";
import { Spin, Empty, message } from "antd";
import ReactECharts from "echarts-for-react";
import client from "../api/client";
import { toSafeString } from "../utils/textDecoder";

/**
 * SessionTrend (v2) — 主播跨场趋势看板（从某场进入）。
 *
 * 路径：/sessions/:id/trend
 * 数据：从 session 拿 anchor_id，再请求：
 *   GET /anchors/:aid/trend           → {anchor_name, total_sessions, items, min_duration_min}
 *   GET /anchors/:aid/top-questions   → {items: [{question, session_count, message_count}]}
 *
 * 简化点（vs 旧 AnchorDashboard）：
 *   - 不实现 LikeChart（M2 再加，依赖 like_event_count 数据源）
 *   - 不实现 TrendNote 详细说明文案（简化）
 */

const LINE_COLORS = {
  composite: "#6366F1",   // 紫
  engagement: "#10B981",  // 绿
  conversion: "#EF4444",  // 红
  content: "#F59E0B",     // 橙
};
const LINE_LABELS = {
  composite: "综合分",
  engagement: "互动分",
  conversion: "转化分",
  content: "内容分",
};

export default function SessionTrend() {
  const { id: sessionId } = useParams();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [trend, setTrend] = useState(null);
  const [questions, setQuestions] = useState(null);
  const [anchorId, setAnchorId] = useState(null);

  // Step 1: 从 session 拿 anchor_id
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    client
      .get(`/sessions/${sessionId}`)
      .then((res) => {
        if (cancelled) return;
        const aid = res?.session?.anchor_id;
        if (!aid) {
          setError("该场次无主播信息");
          setLoading(false);
          return;
        }
        setAnchorId(aid);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || "加载失败");
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [sessionId]);

  // Step 2: 拿 trend + questions
  useEffect(() => {
    if (!anchorId) return;
    let cancelled = false;

    Promise.all([
      client.get(`/anchors/${anchorId}/trend`),
      client.get(`/anchors/${anchorId}/top-questions`),
    ])
      .then(([tRes, qRes]) => {
        if (cancelled) return;
        setTrend(tRes);
        setQuestions(qRes);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || "加载主播数据失败");
          message.error("加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [anchorId]);

  if (loading) {
    return (
      <div style={{ padding: 60, textAlign: "center" }}>
        <Spin size="large" />
      </div>
    );
  }
  if (error) {
    return (
      <section className="session-view">
        <Empty description={error} />
      </section>
    );
  }

  const anchorName = trend?.anchor_name || questions?.anchor_name || "—";
  const totalSessions = trend?.total_sessions ?? 0;
  const trendItems = trend?.items || [];
  const minDurationMin = trend?.min_duration_min ?? 10;
  const questionItems = questions?.items || [];

  return (
    <section className="trend-view">
      <div className="trend-header">
        <div>
          <div className="breadcrumb">{anchorName} · 趋势看板</div>
          <h2 className="session-title">{anchorName} · 跨场进步追踪</h2>
          <div className="session-meta">
            共 {totalSessions} 场 · 仅统计 ≥ {minDurationMin} 分钟有效场次
          </div>
        </div>
      </div>

      {/* 重复问题诊断（如果有）*/}
      <RepeatedQuestionsCard items={questionItems} totalSessions={totalSessions} />

      {/* 趋势图 */}
      {trendItems.length >= 2 ? (
        <TrendChart items={trendItems} />
      ) : (
        <div className="card-block">
          <div className="card-label">综合评分趋势</div>
          <Empty
            description={`累计 ≥ ${minDurationMin} 分钟的场次至少需要 2 场才能画趋势`}
            style={{ padding: 30 }}
          />
        </div>
      )}

      {/* 直播点赞趋势（OCR 抓的本场点赞） */}
      {trendItems.length >= 2 && <LikeChart items={trendItems} />}

      {/* 提问排行 */}
      <TopQuestionsTable items={questionItems} />
    </section>
  );
}

/* ============================================================
   TrendChart — 4 条折线（综合 / 互动 / 转化 / 内容）
   ============================================================ */
function TrendChart({ items }) {
  const option = useMemo(() => {
    const labels = items.map((d) => d.label || `#${d.sequence ?? ""}`);
    const dims = ["composite", "engagement", "conversion", "content"];

    const series = dims.map((dim) => ({
      name: LINE_LABELS[dim],
      type: "line",
      smooth: true,
      symbol: "circle",
      symbolSize: 6,
      data: items.map((d) => (d[dim] != null ? Math.round(d[dim] * 10) / 10 : null)),
      itemStyle: { color: LINE_COLORS[dim] },
      lineStyle: { color: LINE_COLORS[dim], width: dim === "composite" ? 2.5 : 1.5 },
      connectNulls: false,
    }));

    return {
      legend: {
        top: 0,
        right: 10,
        textStyle: { fontSize: 11, color: "#525252" },
      },
      grid: { left: 40, right: 16, top: 36, bottom: 30 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { fontSize: 10, color: "#A3A3A3" },
        axisLine: { lineStyle: { color: "#EAEAEA" } },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        axisLabel: { fontSize: 10, color: "#A3A3A3" },
        splitLine: { lineStyle: { color: "#F0F0F0" } },
      },
      series,
    };
  }, [items]);

  return (
    <div className="card-block" style={{ marginBottom: 16 }}>
      <div className="card-label">综合评分趋势</div>
      <ReactECharts
        option={option}
        style={{ height: 280, width: "100%" }}
        opts={{ renderer: "svg" }}
      />
    </div>
  );
}

/* ============================================================
   LikeChart — 直播点赞趋势（item.like_count，OCR 抓的本场累计点赞）
   ============================================================ */
function formatLike(n) {
  if (n == null) return "—";
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  return String(n);
}

function LikeChart({ items }) {
  // 应用 read-side cummax：OCR 偶发回退，物理上点赞单调递增
  const cleaned = useMemo(() => {
    let cur = -Infinity;
    return items.map((d) => {
      if (d.like_count == null) return { ...d, _like: null };
      cur = Math.max(cur, d.like_count);
      return { ...d, _like: cur };
    });
  }, [items]);

  const hasAny = cleaned.some((d) => d._like != null);

  const option = useMemo(() => {
    return {
      grid: { left: 50, right: 16, top: 24, bottom: 30 },
      tooltip: {
        trigger: "axis",
        formatter: (params) => {
          const p = params[0];
          if (p.value == null) return `${p.name}<br/>未抽到`;
          return `${p.name}<br/>本场点赞 <b>${formatLike(p.value)}</b>`;
        },
      },
      xAxis: {
        type: "category",
        data: cleaned.map((d) => d.label || ""),
        axisLabel: { fontSize: 10, color: "#A3A3A3" },
        axisLine: { lineStyle: { color: "#EAEAEA" } },
      },
      yAxis: {
        type: "value",
        axisLabel: {
          fontSize: 10,
          color: "#A3A3A3",
          formatter: (v) => formatLike(v),
        },
        splitLine: { lineStyle: { color: "#F0F0F0" } },
      },
      series: [
        {
          type: "line",
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          connectNulls: false,
          data: cleaned.map((d) => d._like),
          itemStyle: { color: "#A855F7" },
          lineStyle: { color: "#A855F7", width: 2 },
          areaStyle: {
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(168, 85, 247, 0.18)" },
                { offset: 1, color: "rgba(168, 85, 247, 0)" },
              ],
            },
          },
        },
      ],
    };
  }, [cleaned]);

  return (
    <div className="card-block" style={{ marginBottom: 16 }}>
      <div className="card-label">
        累计点赞趋势
        {!hasAny && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 400,
              color: "var(--text-mute)",
              letterSpacing: 0,
              textTransform: "none",
              marginLeft: 10,
            }}
          >
            · 暂无场次抽到点赞数据（旧 session 或 OCR 未启用 like_count）
          </span>
        )}
      </div>
      {hasAny ? (
        <ReactECharts
          option={option}
          style={{ height: 220, width: "100%" }}
          opts={{ renderer: "svg" }}
        />
      ) : (
        <div className="empty-mini">
          需要重跑 viewer-count 才能填充 like_count（M2 历史回填）
        </div>
      )}
    </div>
  );
}

/* ============================================================
   TopQuestionsTable — 观众提问排行
   ============================================================ */
function TopQuestionsTable({ items }) {
  if (!items || items.length === 0) {
    return (
      <div className="card-block">
        <div className="card-label">观众提问排行</div>
        <Empty description="暂无提问数据" style={{ padding: 30 }} />
      </div>
    );
  }
  return (
    <div className="card-block">
      <div className="card-label">观众提问排行</div>
      <div className="trend-questions">
        <div className="trend-q-header">
          <span>#</span>
          <span>问题</span>
          <span>出现场次</span>
          <span>总次数</span>
        </div>
        {items.map((item, idx) => (
          <div key={item.question} className="trend-q-row">
            <span className={`trend-q-rank rank-${Math.min(idx, 2)}`}>
              {idx + 1}
            </span>
            <span className="trend-q-text">{toSafeString(item.question)}</span>
            <span className="trend-q-sc">{item.session_count} 场</span>
            <span className="trend-q-mc">{item.message_count} 次</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============================================================
   RepeatedQuestionsCard — 反复出现的问题 + 规则诊断
   ============================================================ */

const REPEAT_RULES = [
  {
    keywords: ["多少钱", "价格", "多少", "什么价", "几块"],
    diagnosis: "用户反复问价格，说明价格锚点没立住",
    action: "下场开头 5 分钟内明确报「原价 vs 直播价」，并在每个产品讲解时重复一次",
  },
  {
    keywords: ["怎么下单", "在哪买", "怎么买", "链接", "购买", "下单"],
    diagnosis: "购买路径不清晰",
    action: "讲完每个产品后，明确说「购物车第 X 个链接」，配合手势指引",
  },
  {
    keywords: ["发货", "多久到", "几天到", "物流", "快递", "包邮"],
    diagnosis: "物流问题反复被问",
    action: "在置顶/简介里固定写发货时效，开场口播一次",
  },
  {
    keywords: ["适合", "什么肤质", "敏感肌", "油皮", "干皮", "混合肌"],
    diagnosis: "肤质适配信息不足",
    action: "讲解每个产品时口头说「适合 XX 肤质 / 不建议 XX 肤质」",
  },
  {
    keywords: ["有没有", "现货", "库存", "还有吗"],
    diagnosis: "库存信息缺失",
    action: "讲产品时同步说「剩余 X 件 / 充足」，制造紧迫感",
  },
  {
    keywords: ["怎么用", "用法", "怎么涂", "步骤", "教程"],
    diagnosis: "用法教学不到位",
    action: "示范每个产品的使用步骤，控制在 60 秒内",
  },
  {
    keywords: ["哪个颜色", "什么色", "几号色", "色号"],
    diagnosis: "色号选择困惑",
    action: "讲解时按肤色推荐色号，准备色卡对比",
  },
];

const MIN_SESSION_COUNT = 3;

function RepeatedQuestionsCard({ items, totalSessions }) {
  const recurring = (items || []).filter(
    (item) => item.session_count >= MIN_SESSION_COUNT
  );

  // 匹配规则
  const matched = useMemo(() => {
    const out = [];
    for (const item of recurring) {
      const q = item.question || "";
      const rule = REPEAT_RULES.find((r) =>
        r.keywords.some((kw) => q.includes(kw))
      );
      if (rule) out.push({ item, rule });
      if (out.length >= 3) break; // 最多 3 条
    }
    return out;
  }, [recurring]);

  if (matched.length === 0) return null;

  return (
    <div className="repeated-q-card">
      <div className="repeated-q-label">
        🔁 这些问题在 {recurring.length > 1 ? `近 ${totalSessions} 场` : ""}反复出现
      </div>
      <div className="repeated-q-list">
        {matched.map(({ item, rule }, i) => (
          <div key={i} className="repeated-q-item">
            <div className="repeated-q-row1">
              <span className="repeated-q-text">"{toSafeString(item.question)}"</span>
              <span className="repeated-q-meta">
                {item.session_count} 场 · {item.message_count} 次
              </span>
            </div>
            <div className="repeated-q-diagnosis">{rule.diagnosis}</div>
            <div className="repeated-q-action">
              <span className="opt-action-tag">行动</span>
              {rule.action}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
