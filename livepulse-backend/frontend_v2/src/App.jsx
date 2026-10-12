import { Routes, Route, Navigate } from "react-router-dom";
import { ConfigProvider, App as AntdApp, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import RequireAuth from "./components/RequireAuth";
import { IconSprite } from "./components/layout/IconSprite";
import AppShell from "./components/layout/AppShell";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Home from "./pages/Home";
import AnchorSessions from "./pages/AnchorSessions";
import Upload from "./pages/Upload";
import SessionReport from "./pages/SessionReport";
import SessionWorkspace from "./pages/SessionWorkspace";
import SessionTrend from "./pages/SessionTrend";
import Profile from "./pages/Profile";
import Placeholder from "./pages/Placeholder";

export default function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: "#6366F1",   // 跟 UI Lab 一致：紫色（Indigo）
          borderRadius: 6,
          fontFamily:
            "'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif",
        },
      }}
    >
      <AntdApp>
        <IconSprite />
        <Routes>
          {/* Public */}
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />

          {/* Protected — all wrapped in AppShell */}
          <Route element={<RequireAuth><AppShell /></RequireAuth>}>
            <Route path="/" element={<Home />} />
            <Route path="/anchors/:anchorId" element={<AnchorSessions />} />
            <Route path="/sessions/:id/report" element={<SessionReport />} />
            <Route path="/sessions/:id/workspace" element={<SessionWorkspace />} />
            <Route path="/sessions/:id/trend" element={<SessionTrend />} />
            <Route
              path="/sessions/:id"
              element={<Navigate to="report" replace />}
            />
            <Route path="/upload" element={<Upload />} />
            <Route
              path="/credits"
              element={
                <Placeholder
                  iconName="credit"
                  title="积分套餐"
                  text="M2 功能 · 查看余额、购买套餐"
                  eta="M2"
                />
              }
            />
            <Route path="/profile" element={<Profile />} />
            <Route
              path="/notifications"
              element={
                <Placeholder
                  iconName="mail"
                  title="系统提示"
                  text="M2 功能 · 分析完成通知、新功能预告"
                  eta="M2"
                />
              }
            />
          </Route>

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AntdApp>
    </ConfigProvider>
  );
}
