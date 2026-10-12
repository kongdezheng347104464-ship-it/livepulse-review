import { useMemo } from "react";
import ReactECharts from "echarts-for-react";

/**
 * KeywordDensityCard (v2) — 每分钟字数 + 关键词热度
 *
 * 纯前端衍生 from scriptSegments，跟旧 frontend 同 tokenize 规则。
 * 改成 v2 紫色配色 + 直接 echarts-for-react（不走 BaseChart 抽象）。
 */

const STOPWORDS = new Set([
  "的", "了", "在", "是", "我", "你", "他", "她", "它", "我们", "你们", "他们",
  "这", "那", "这个", "那个", "这样", "那样", "这里", "那里",
  "就", "都", "也", "还", "又", "再", "已", "已经", "正在", "将",
  "好", "对", "啊", "呀", "吗", "呢", "吧", "哦", "嗯", "哈", "哎", "诶",
  "把", "和", "与", "及", "或", "但", "但是", "可是", "如果", "因为",
  "所以", "然后", "比如", "那么", "话说",
  "什么", "怎么", "为什么", "多少", "怎样", "哪里", "哪个",
  "知道", "不知道", "可以", "不行", "行", "要", "不要", "想", "不想",
  "有", "没有", "没", "不", "真的", "真", "好的",
  "姐妹", "姐妹们", "大家", "宝宝", "宝宝们", "亲", "亲们",
  "一个", "一下", "一点", "一些", "一会", "一次",
  "现在", "今天", "之后", "之前", "马上",
  "啊啊", "嗯嗯", "哎呀", "哎哟",
]);

function tokenizeAndCount(text) {
  if (!text) return {};
  const counts = {};
  const cnPhrases = text.match(/[一-龥]{2,4}/g) || [];
  for (const p of cnPhrases) {
    if (STOPWORDS.has(p)) continue;
    if (p.length < 2) continue;
    counts[p] = (counts[p] || 0) + 1;
  }
  return counts;
}

export default function KeywordDensityCard({ scriptSegments, durationSec }) {
  const { perMinute, summary } = useMemo(() => {
    if (!scriptSegments?.length) return { perMinute: [], summary: null };
    const totalMinutes = Math.max(1, Math.ceil((durationSec || 0) / 60));
    const buckets = new Array(totalMinutes).fill(null).map(() => ({
      charCount: 0,
      segCount: 0,
      keywords: {},
    }));

    for (const seg of scriptSegments) {
      const mb = Math.floor((seg.start_sec || 0) / 60);
      if (mb < 0 || mb >= buckets.length) continue;
      const text = (seg.text || "").trim();
      buckets[mb].charCount += text.length;
      buckets[mb].segCount += 1;
      const local = tokenizeAndCount(text);
      for (const [k, v] of Object.entries(local)) {
        buckets[mb].keywords[k] = (buckets[mb].keywords[k] || 0) + v;
      }
    }

    const perMinute = buckets.map((b, i) => ({
      minute: i,
      charCount: b.charCount,
      segCount: b.segCount,
      topKeywords: Object.entries(b.keywords)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3),
    }));

    const totalChars = perMinute.reduce((s, b) => s + b.charCount, 0);
    const avgChars = totalChars / Math.max(perMinute.length, 1);
    const peakBucket = perMinute.reduce(
      (best, b) => (b.charCount > (best?.charCount || 0) ? b : best),
      null
    );

    return {
      perMinute,
      summary: {
        avgChars: Math.round(avgChars),
        peakMinute: peakBucket?.minute ?? 0,
        peakChars: peakBucket?.charCount ?? 0,
      },
    };
  }, [scriptSegments, durationSec]);

  const option = useMemo(() => {
    if (!perMinute.length) return null;
    return {
      grid: { left: 50, right: 16, top: 16, bottom: 28 },
      tooltip: {
        trigger: "axis",
        formatter: (params) => {
          const p = Array.isArray(params) ? params[0] : params;
          const b = perMinute[p.dataIndex];
          if (!b) return "";
          const kw = b.topKeywords.length
            ? b.topKeywords.map(([k, n]) => `${k}·${n}`).join(" ")
            : "—";
          return `第 ${b.minute + 1} 分钟 · <b>${b.charCount}</b> 字 · ${b.segCount} 段<br/>关键词: ${kw}`;
        },
      },
      xAxis: {
        type: "category",
        data: perMinute.map((b) => `${b.minute + 1}'`),
        axisLabel: { color: "#A3A3A3", fontSize: 10 },
        axisLine: { lineStyle: { color: "#EAEAEA" } },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: "#A3A3A3", fontSize: 10 },
        splitLine: { lineStyle: { color: "#F0F0F0", type: "dashed" } },
      },
      series: [
        {
          type: "bar",
          data: perMinute.map((b) => b.charCount),
          itemStyle: {
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "#6366F1" },
                { offset: 1, color: "rgba(99, 102, 241, 0.2)" },
              ],
            },
            borderRadius: [3, 3, 0, 0],
          },
          barMaxWidth: 24,
        },
      ],
    };
  }, [perMinute]);

  if (!scriptSegments?.length) return null;

  return (
    <div className="card-block" style={{ marginTop: 16 }}>
      <div
        className="card-label"
        style={{ display: "flex", alignItems: "baseline", gap: 12 }}
      >
        话术密度（每分钟字数 + 关键词热度）
        {summary && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 400,
              color: "var(--text-mute)",
              letterSpacing: 0,
              textTransform: "none",
            }}
          >
            平均 {summary.avgChars} 字/分 · 第 {summary.peakMinute + 1} 分钟最高{" "}
            {summary.peakChars} 字
          </span>
        )}
      </div>
      <ReactECharts
        option={option}
        style={{ height: 220, width: "100%" }}
        opts={{ renderer: "svg" }}
      />
    </div>
  );
}
