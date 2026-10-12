import { useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { Form, Input, Button, message } from "antd";
import client from "../api/client";
import useAuthStore from "../store/authStore";

export default function Login() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const next = searchParams.get("next") || "/";
  const setToken = useAuthStore((s) => s.setToken);
  const setUser = useAuthStore((s) => s.setUser);
  const [loading, setLoading] = useState(false);

  async function handleLogin(values) {
    setLoading(true);
    try {
      const res = await client.post("/auth/login", values);
      setToken(res.access_token);
      const me = await client.get("/auth/me");
      setUser(me);
      message.success("登录成功");
      navigate(next, { replace: true });
    } catch (e) {
      message.error(e.message || "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      {/* 左侧 brand panel — 紫色烟雾背景图 + LOGO + slogan */}
      <aside className="auth-brand-panel">
        <div className="auth-brand-panel-inner">
          <div className="auth-brand-panel-top">
            <img
              src="/logo.png?v=2"
              alt="LivePulse"
              className="auth-brand-panel-logo"
            />
          </div>
          <div className="auth-brand-panel-bottom">
            <div className="auth-brand-headline">
              每一场直播
              <br />
              都有迹可循
            </div>
          </div>
        </div>
      </aside>

      {/* 右侧表单 */}
      <div className="auth-card">
        <h2 className="auth-title">欢迎回来</h2>
        <p className="auth-subtitle">登录你的 LivePulse 账号，继续复盘每一场直播。</p>

        <Form layout="vertical" onFinish={handleLogin} requiredMark={false}>
          <Form.Item
            label="邮箱"
            name="email"
            rules={[{ required: true, message: "请输入邮箱" }]}
          >
            <Input placeholder="you@livepulse.app" autoComplete="email" size="large" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password placeholder="••••••••" autoComplete="current-password" size="large" />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            block
            loading={loading}
            size="large"
            className="auth-submit"
          >
            登录
          </Button>
        </Form>

        <div className="auth-footer">
          还没有账号？ <Link to="/register">注册</Link>
        </div>
      </div>
    </div>
  );
}
