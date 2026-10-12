import { useEffect, useState } from "react";
import { Drawer, Spin, Empty, Select, message, Tag } from "antd";
import client from "../api/client";
import { toSafeString } from "../utils/textDecoder";

/* ============================================================
   3 个 Drawer (v2)：
     - StrategyDrawer        点话术策略 → GET /sessions/:id/strategies
     - IntentDanmakuDrawer   点漏斗层 → GET /sessions/:id/intent-details
     - ConversionEvidenceDrawer 点转化触发卡 → GET /sessions/:id/danmaku-window

   注意：v2 client.js auto-unwrap envelope，所以直接 res.items / res.strategies。
   ============================================================ */

const SORT_OPTIONS = [
  { value: "conversion", label: "按转化分" },
  { value: "time", label: "按时间" },
];

const SENTIMENT_COLORS = {
  positive: "#10B981",
  neutral: "#A3A3A3",
  negative: "#EF4444",
};
const SENTIMENT_LABELS = {
  positive: "正面",
  neutral: "中性",
  negative: "负面",
};

function fmtTime(sec) {
  if (sec == null) return "00:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/* ---------- 1. StrategyDrawer (话术样本) ---------- */
export function StrategyDrawer({ open, title, sessionId, tactic, onClose }) {
  const [loading, setLoading] = useState(false);
  const [strategies, setStrategies] = useState([]);
  const [sortBy, setSortBy] = useState("conversion");

  useEffect(() => {
    if (!open || !sessionId) return;
    setLoading(true);
    const params = { sort_by: sortBy, limit: 15 };
    if (tactic) params.tactic = tactic;
    client
      .get(`/sessions/${sessionId}/strategies`, { params })
      .then((res) => setStrategies(res?.strategies || []))
      .catch((err) => {
        setStrategies([]);
        message.error(`加载话术失败: ${err.message}`);
      })
      .finally(() => setLoading(false));
  }, [open, sessionId, tactic, sortBy]);

  return (
    <Drawer
      title={title || "话术样本"}
      placement="right"
      width={520}
      open={open}
      onClose={onClose}
      styles={{ body: { padding: "16px 20px", background: "var(--bg)" } }}
    >
      <div style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-mute)" }}>排序：</span>
        <Select
          size="small"
          value={sortBy}
          onChange={setSortBy}
          options={SORT_OPTIONS}
          style={{ width: 110 }}
        />
      </div>

      {loading ? (
        <Center><Spin /></Center>
      ) : strategies.length === 0 ? (
        <Empty description="该策略暂无话术样本" />
      ) : (
        strategies.map((group, gi) => (
          <div key={gi} style={{ marginBottom: 16 }}>
            {group.label && (
              <div className="drawer-group-label">
                {toSafeString(group.label)}{" "}
                <span style={{ color: "var(--text-mute)", fontWeight: 400 }}>
                  · {group.examples?.length || 0} 条
                </span>
              </div>
            )}
            {(group.examples || []).map((ex, ei) => (
              <div key={ei} className="drawer-script-card">
                <div className="drawer-script-time">
                  {fmtTime(ex.start_sec)} – {fmtTime(ex.end_sec)}
                </div>
                <div className="drawer-script-text">
                  {toSafeString(ex.text)}
                </div>
                {ex.metrics && (
                  <div className="drawer-script-metrics">
                    {ex.metrics.danmaku_count != null && (
                      <span>弹幕 {ex.metrics.danmaku_count}</span>
                    )}
                    {ex.metrics.purchase_intents != null && (
                      <span>购买意图 {ex.metrics.purchase_intents}</span>
                    )}
                    {ex.metrics.score != null && (
                      <span>影响力 {Math.round(ex.metrics.score)}</span>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        ))
      )}
    </Drawer>
  );
}

/* ---------- 2. IntentDanmakuDrawer (漏斗某层弹幕) ---------- */
export function IntentDanmakuDrawer({ open, title, sessionId, intent, onClose }) {
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState([]);
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (!open || !sessionId) return;
    setLoading(true);
    client
      .get(`/sessions/${sessionId}/intent-details`, {
        params: { intent, limit: 50 },
      })
      .then((res) => {
        setItems(res?.items || []);
        setCount(res?.count || 0);
      })
      .catch((err) => {
        setItems([]);
        setCount(0);
        message.error(`加载弹幕失败: ${err.message}`);
      })
      .finally(() => setLoading(false));
  }, [open, sessionId, intent]);

  return (
    <Drawer
      title={title || "弹幕详情"}
      placement="right"
      width={520}
      open={open}
      onClose={onClose}
      styles={{ body: { padding: "16px 20px", background: "var(--bg)" } }}
    >
      <div
        style={{
          marginBottom: 12,
          fontSize: 12,
          color: "var(--text-mute)",
        }}
      >
        共 {count} 条 · 展示前 {items.length}
      </div>

      {loading ? (
        <Center><Spin /></Center>
      ) : items.length === 0 ? (
        <Empty description="无相关弹幕" />
      ) : (
        items.map((d, i) => (
          <div key={i} className="drawer-danmaku-card">
            <div className="drawer-danmaku-head">
              <span className="drawer-danmaku-time">
                {fmtTime(d.timestamp_sec)}
              </span>
              {d.normalized_username && (
                <span className="drawer-danmaku-user">
                  {toSafeString(d.normalized_username)}
                </span>
              )}
              {d.sentiment && (
                <Tag
                  color={SENTIMENT_COLORS[d.sentiment]}
                  style={{ fontSize: 10, lineHeight: "16px", padding: "0 6px" }}
                >
                  {SENTIMENT_LABELS[d.sentiment] || d.sentiment}
                </Tag>
              )}
            </div>
            <div className="drawer-danmaku-content">
              {toSafeString(d.content)}
            </div>
          </div>
        ))
      )}
    </Drawer>
  );
}

/* ---------- 3. ConversionEvidenceDrawer (转化触发证据) ---------- */
export function ConversionEvidenceDrawer({ open, trigger, sessionId, onClose }) {
  const [loading, setLoading] = useState(false);
  const [danmakus, setDanmakus] = useState([]);

  useEffect(() => {
    if (!open || !sessionId || !trigger) return;
    setLoading(true);
    const start = trigger.timeRange?.start_sec || 0;
    const end = trigger.timeRange?.end_sec || start + 30;
    client
      .get(`/sessions/${sessionId}/danmaku-window`, {
        params: { start_sec: start, end_sec: end, limit: 50 },
      })
      .then((res) => setDanmakus(res?.danmakus || []))
      .catch((err) => {
        setDanmakus([]);
        message.error(`加载证据失败: ${err.message}`);
      })
      .finally(() => setLoading(false));
  }, [open, sessionId, trigger]);

  if (!trigger) return null;

  return (
    <Drawer
      title="高转化话术 · 证据链"
      placement="right"
      width={560}
      open={open}
      onClose={onClose}
      styles={{ body: { padding: "16px 20px", background: "var(--bg)" } }}
    >
      {/* 触发卡片本身 */}
      <div className="drawer-trigger-summary">
        <div className="drawer-trigger-rank">#{trigger.rank}</div>
        <div className="drawer-trigger-title">
          {toSafeString(trigger.summary || trigger.script)}
        </div>
        <div className="drawer-trigger-time">
          {fmtTime(trigger.timeRange?.start_sec)} –{" "}
          {fmtTime(trigger.timeRange?.end_sec)}
        </div>
      </div>

      {/* metrics 三件套 */}
      <div className="drawer-metrics-row">
        <Metric label="影响力" value={trigger.score} />
        <Metric label="强购买" value={trigger.strongPurchaseCount} />
        <Metric label="逼单次数" value={trigger.closingTactics} />
      </div>

      {/* 原话 */}
      {trigger.script && (
        <div className="drawer-script-quote">
          <div className="drawer-quote-label">主播原话</div>
          <div className="drawer-quote-text">{toSafeString(trigger.script)}</div>
        </div>
      )}

      {/* 时间窗弹幕 */}
      <div style={{ marginTop: 16 }}>
        <div className="drawer-group-label">同时段弹幕响应</div>
        {loading ? (
          <Center><Spin /></Center>
        ) : danmakus.length === 0 ? (
          <Empty description="该时段无弹幕" />
        ) : (
          danmakus.map((d, i) => (
            <div key={i} className="drawer-danmaku-card">
              <div className="drawer-danmaku-head">
                <span className="drawer-danmaku-time">
                  {fmtTime(d.timestamp_sec)}
                </span>
                {d.normalized_username && (
                  <span className="drawer-danmaku-user">
                    {toSafeString(d.normalized_username)}
                  </span>
                )}
                {d.intent && (
                  <Tag
                    style={{ fontSize: 10, lineHeight: "16px", padding: "0 6px" }}
                    color="purple"
                  >
                    {d.intent}
                  </Tag>
                )}
              </div>
              <div className="drawer-danmaku-content">
                {toSafeString(d.content)}
              </div>
            </div>
          ))
        )}
      </div>
    </Drawer>
  );
}

/* ---------- helpers ---------- */
function Center({ children }) {
  return (
    <div style={{ textAlign: "center", padding: 40 }}>{children}</div>
  );
}

function Metric({ label, value }) {
  return (
    <div className="drawer-metric">
      <div className="drawer-metric-label">{label}</div>
      <div className="drawer-metric-value">{value ?? "—"}</div>
    </div>
  );
}
