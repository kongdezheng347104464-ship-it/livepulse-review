import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Form, Input, Button, message } from "antd";
import client from "../api/client";
import useAuthStore from "../store/authStore";

export default function Register() {
  const navigate = useNavigate();
  const setToken = useAuthStore((s) => s.setToken);
  const setUser = useAuthStore((s) => s.setUser);
  const [loading, setLoading] = useState(false);

  async function handleRegister(values) {
    setLoading(true);
    try {
      const res = await client.post("/auth/register", values);
      setToken(res.access_token);
      const me = await client.get("/auth/me");
      setUser(me);
      message.success("注册成功");
      navigate("/", { replace: true });
    } catch (e) {
      message.error(e.message || "注册失败");
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
        <h2 className="auth-title">创建账号</h2>
        <p className="auth-subtitle">注册 LivePulse 账号，把每一场直播变成可被看懂的复盘报告。</p>

        <Form layout="vertical" onFinish={handleRegister} requiredMark={false}>
          <Form.Item
            label="邮箱"
            name="email"
            rules={[
              { required: true, message: "请输入邮箱" },
              { type: "email", message: "邮箱格式不正确" },
            ]}
          >
            <Input placeholder="you@livepulse.app" autoComplete="email" size="large" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[
              { required: true, message: "请输入密码" },
              { min: 8, message: "密码至少 8 位" },
            ]}
            extra="至少 8 位，含字母 + 数字"
          >
            <Input.Password placeholder="••••••••" autoComplete="new-password" size="large" />
          </Form.Item>
          <Form.Item
            label="邀请码"
            name="invitation_code"
            rules={[{ required: true, message: "请输入邀请码" }]}
          >
            <Input placeholder="16 位邀请码" size="large" maxLength={16} />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            block
            loading={loading}
            size="large"
            className="auth-submit"
          >
            注册
          </Button>
        </Form>

        <div className="auth-footer">
          已有账号？ <Link to="/login">登录</Link>
        </div>
      </div>
    </div>
  );
}
