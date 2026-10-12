import { useEffect, useRef } from "react";
import useSessionStore from "../store/sessionStore";

/**
 * useSessionData (v2) — 跟旧 frontend 一致：
 *   1. 首挂载 → fetch full session
 *   2. 非终态 → 每 5s 轮询 /status
 *   3. status 翻 terminal → 拉一次完整 session 后停轮询
 */

const TERMINAL_STATUSES = new Set(["analyzed", "done", "error", "failed"]);
const POLL_INTERVAL_MS = 5000;

export default function useSessionData(sessionId) {
  const session = useSessionStore((s) => s.session);
  const scriptSegments = useSessionStore((s) => s.scriptSegments);
  const danmakus = useSessionStore((s) => s.danmakus);
  const loading = useSessionStore((s) => s.loading);
  const error = useSessionStore((s) => s.error);
  const progress = useSessionStore((s) => s.progress);
  const historicalComparison = useSessionStore((s) => s.historicalComparison);
  const fetchSession = useSessionStore((s) => s.fetchSession);
  const fetchStatus = useSessionStore((s) => s.fetchStatus);
  const reset = useSessionStore((s) => s.reset);

  const detailLoadedRef = useRef(false);

  // 切换 session 时 reset store + 立刻拉 session + status（并行）
  // 立刻拉 status 是为了避免 processing 状态下进度页全部 queued 显示
  // ——5s polling 才第一次到的话用户会以为 pipeline 没启动
  useEffect(() => {
    if (!sessionId) return;
    detailLoadedRef.current = false;
    reset();
    fetchSession(sessionId).then(() => {
      detailLoadedRef.current = true;
    });
    fetchStatus(sessionId).catch(() => {
      /* status 拉失败不致命，5s polling 会再试 */
    });
  }, [sessionId, fetchSession, fetchStatus, reset]);

  useEffect(() => {
    if (!sessionId) return;

    const effective = progress?.status || session?.status;
    if (effective && TERMINAL_STATUSES.has(effective)) return;

    // 进 processing 页面时缩短首轮间隔（1.5s）让用户更快看到第一个 step
    // 之后用 5s 节流避免打 backend
    let isFirstTick = true;
    const tick = async () => {
      const status = await fetchStatus(sessionId);
      if (status && TERMINAL_STATUSES.has(status.status)) {
        clearInterval(interval);
        try {
          await fetchSession(sessionId);
        } catch (e) {
          console.error("[useSessionData] post-pipeline refresh failed:", e);
        }
      }
      if (isFirstTick) {
        // 第一次 tick 完后切到常规 5s 间隔
        clearInterval(interval);
        interval = setInterval(tick, POLL_INTERVAL_MS);
        isFirstTick = false;
      }
    };
    let interval = setInterval(tick, 1500);

    return () => clearInterval(interval);
  }, [sessionId, session?.status, progress?.status, fetchStatus, fetchSession]);

  return {
    session,
    scriptSegments,
    danmakus,
    loading,
    error,
    progress,
    historicalComparison,
  };
}
