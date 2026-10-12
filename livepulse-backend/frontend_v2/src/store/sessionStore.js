import { create } from "zustand";
import client from "../api/client";

/**
 * sessionStore (v2) — 跟旧 frontend 行为一致，但适配 v2 client.js 的
 * auto-unwrap envelope（所以 res 直接是 data 层，不需要 .data 解第二层）。
 *
 * 当前不实现 cross-chart filters（getFilteredSegments 等），W3 现阶段
 * 用不到。等真正需要交互时再加。
 */
const useSessionStore = create((set, get) => ({
  session: null,
  scriptSegments: [],
  danmakus: [],
  loading: true,
  error: null,
  progress: null,
  historicalComparison: null,

  fetchSession: async (sessionId) => {
    set({ loading: true, error: null });
    try {
      const res = await client.get(`/sessions/${sessionId}`);
      set({
        session: {
          ...res.session,
          viewer_snapshots: res.viewer_snapshots || [],
        },
        scriptSegments: res.script_segments || [],
        danmakus: res.danmakus || [],
        historicalComparison: res.historical_comparison || null,
        loading: false,
      });
    } catch (err) {
      set({ error: err.message, loading: false });
    }
  },

  fetchStatus: async (sessionId) => {
    try {
      const res = await client.get(`/sessions/${sessionId}/status`);
      set({ progress: res });
      return res;
    } catch (err) {
      set({ error: err.message });
      return null;
    }
  },

  reset: () => {
    set({
      session: null,
      scriptSegments: [],
      danmakus: [],
      progress: null,
      historicalComparison: null,
      loading: true,
      error: null,
    });
  },
}));

export default useSessionStore;
