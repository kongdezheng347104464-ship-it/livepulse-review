import { useEffect, useState } from "react";
import client from "../api/client";
import useAuthStore from "../store/authStore";

/**
 * Home — 登录后默认页。
 * 欢迎语 + slogan + 数据总览（30 天 / 最新评分 / 历史最佳 / 平均时长）。
 */
export default function Home() {
  const user = useAuthStore((s) => s.user);
  const username = user?.email?.split("@")[0] || "User";
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        // 现有后端没有专用 overview endpoint，先用 /sessions/ 拉一页计算
        // W2 阶段可以加 GET /overview 后端 endpoint，更高效
        const res = await client.get("/sessions/", { params: { page_size: 50 } });
        if (cancelled) return;
        const items = res?.items || [];
        const analyzed = items.filter((s) => s.status === "analyzed");
        const scores = analyzed
          .map((s) => s.composite_score)
          .filter((x) => x != null && x > 0);
        const durations = analyzed
          .map((s) => s.duration_sec)
          .filter((x) => x != null && x > 0);
        setStats({
          sessions_30d: analyzed.length,
          latest_score: scores.length ? Math.round(scores[0]) : "—",
          best_score: scores.length ? Math.round(Math.max(...scores)) : "—",
          avg_duration_min: durations.length
            ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length / 60)
            : "—",
        });
      } catch (e) {
        console.error("Failed to load stats:", e);
        setStats({ sessions_30d: "—", latest_score: "—", best_score: "—", avg_duration_min: "—" });
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  return (
    <section className="home">
      <div className="hero">
        <div className="hero-text">
          <h1>
            Hello, <span className="hero-name">{username}</span>!
          </h1>
          <p className="hero-sub">It's good to see you again.</p>
        </div>
        <div className="hero-robot">
          <img src="/hero.png?v=2" alt="AI 教练" />
        </div>
      </div>

      <div className="slogan-wrap">
        <div className="slogan-mark" />
        <div className="slogan">每一场直播都有迹可循</div>
        <div className="slogan-sub">Every live, a pattern to trace.</div>
      </div>

      {stats && (
        <div className="stats-grid">
          <div className="stat-card stat-emphasis">
            <div className="stat-num">{stats.sessions_30d}</div>
            <div className="stat-label">已分析场次</div>
            <div className="stat-trend">基于真实数据</div>
          </div>
          <div className="stat-card">
            <div className="stat-num">{stats.latest_score}</div>
            <div className="stat-label">最新综合评分</div>
            <div className="stat-trend muted">最近一场</div>
          </div>
          <div className="stat-card">
            <div className="stat-num">{stats.best_score}</div>
            <div className="stat-label">历史最佳</div>
            <div className="stat-trend muted">scoring v5</div>
          </div>
          <div className="stat-card">
            <div className="stat-num">
              {stats.avg_duration_min}
              <span className="stat-unit">分</span>
            </div>
            <div className="stat-label">平均时长</div>
            <div className="stat-trend muted">analyzed only</div>
          </div>
        </div>
      )}

      <div className="home-hint">从左侧选择主播查看场次</div>
    </section>
  );
}
