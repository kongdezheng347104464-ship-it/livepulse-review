import axios from "axios";
import useAuthStore from "../store/authStore";

/**
 * frontend_v2 — HTTP client（从旧 frontend 平移过来，逻辑不变）
 *
 * Auth source (priority):
 *   1. JWT from authStore → Authorization: Bearer
 *   2. VITE_API_KEY env → X-API-Key (dev fallback)
 *
 * 401 handling: clear token + redirect to /login (avoid loops).
 */

const API_KEY_FALLBACK = import.meta.env.VITE_API_KEY || "";

const client = axios.create({
  baseURL: "/api/v1",
  timeout: 30000,
  headers: { "Content-Type": "application/json" },
});

client.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers["Authorization"] = `Bearer ${token}`;
  } else if (API_KEY_FALLBACK) {
    config.headers["X-API-Key"] = API_KEY_FALLBACK;
  }
  return config;
});

client.interceptors.response.use(
  (res) => {
    // Backend 统一 envelope: { code: 0, data: ..., message: "ok" }
    // 自动 unwrap data 层，让 page code 直接用 res.items / res.access_token，
    // 不需要每个调用都写 res.data.items（旧 frontend 的写法）。
    const body = res.data;
    if (
      body &&
      typeof body === "object" &&
      "code" in body &&
      "data" in body
    ) {
      return body.data;
    }
    return body;
  },
  (err) => {
    const status = err.response?.status;
    const detail = err.response?.data?.detail;
    const url = err.config?.url || "";

    if (status === 401 && !url.startsWith("/auth/")) {
      useAuthStore.getState().logout();
      if (!window.location.pathname.startsWith("/login")) {
        const redirect = encodeURIComponent(
          window.location.pathname + window.location.search
        );
        window.location.assign(`/login?next=${redirect}`);
      }
    }

    const msg =
      (typeof detail === "string" ? detail : null) ||
      err.message ||
      "请求失败";
    const wrapped = new Error(msg);
    wrapped.status = status;
    wrapped.detail = detail;
    return Promise.reject(wrapped);
  }
);

export default client;
