import { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Spin } from "antd";
import client from "../api/client";
import useAuthStore from "../store/authStore";

/**
 * RequireAuth — 跟旧 frontend 实现一致，复用 /auth/me 校验。
 * 行为不变，只是从 v2 项目里重新走一遍。
 */
export default function RequireAuth({ children }) {
  const location = useLocation();
  const token = useAuthStore((s) => s.token);
  const user = useAuthStore((s) => s.user);
  const bootChecked = useAuthStore((s) => s.bootChecked);
  const setUser = useAuthStore((s) => s.setUser);
  const markBootChecked = useAuthStore((s) => s.markBootChecked);

  useEffect(() => {
    let cancelled = false;
    async function verify() {
      if (!token) {
        markBootChecked();
        return;
      }
      try {
        const me = await client.get("/auth/me");
        if (!cancelled) setUser(me);
      } catch {
        /* 401 → client.js 已经跳转 /login */
      } finally {
        if (!cancelled) markBootChecked();
      }
    }
    if (!bootChecked) verify();
    return () => {
      cancelled = true;
    };
  }, [token, bootChecked, setUser, markBootChecked]);

  if (!token) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  if (!bootChecked) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  if (!user) return null;

  return children;
}
