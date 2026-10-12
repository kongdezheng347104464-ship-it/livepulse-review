import { useState, useEffect } from "react";
import { useNavigate, useLocation, useParams, Outlet, matchPath } from "react-router-dom";
import client from "../../api/client";
import PrimarySidebar from "./PrimarySidebar";
import SecondarySidebar from "./SecondarySidebar";
import TopBar from "./TopBar";

/**
 * AppShell — 顶层布局壳。
 *
 * 状态管理（owner 钉死的规则）：
 *   - sidebarCollapsed: 一级侧栏折叠
 *   - subbarOpen: 二级侧栏是否显示，跟当前路由半独立
 *
 * 路由 → subbar 关系：
 *   /sessions/:id/report|workspace|trend  → subbarOpen=true
 *   切到主播场次列表 → subbarOpen 不变（保持上一次）
 *   切到 credits/profile/notif/home → subbarOpen=false
 */
export default function AppShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [subbarOpen, setSubbarOpen] = useState(false);
  const [anchors, setAnchors] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [activeAnchorId, setActiveAnchorId] = useState(null);

  // 拉主播列表，提到 effect 外供多处调用
  async function loadAnchors() {
    try {
      const res = await client.get("/anchors/", { params: { page_size: 100 } });
      setAnchors(res?.items || []);
    } catch (e) {
      console.error("Failed to load anchors:", e);
    }
  }

  useEffect(() => { loadAnchors(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // 主播改名后同步更新侧边栏（本地改，不重拉）
  useEffect(() => {
    const handler = (e) => {
      const { id, name } = e.detail || {};
      if (!id || !name) return;
      setAnchors((prev) => prev.map((a) => (a.id === id ? { ...a, name } : a)));
    };
    window.addEventListener("anchor:renamed", handler);
    return () => window.removeEventListener("anchor:renamed", handler);
  }, []);

  // 新建主播后重新拉列表，保证排序和字段与后端一致
  useEffect(() => {
    const handler = () => loadAnchors();
    window.addEventListener("anchor:created", handler);
    return () => window.removeEventListener("anchor:created", handler);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // 删除主播后移除条目；如果当前正在浏览该主播则跳回首页
  useEffect(() => {
    const handler = (e) => {
      const { id } = e.detail || {};
      if (!id) return;
      setAnchors((prev) => prev.filter((a) => a.id !== id));
      if (location.pathname.startsWith(`/anchors/${id}`) ||
          location.pathname.includes(`/sessions/`) && activeAnchorId === id) {
        navigate("/");
      }
    };
    window.addEventListener("anchor:deleted", handler);
    return () => window.removeEventListener("anchor:deleted", handler);
  }, [location.pathname, activeAnchorId, navigate]);

  // 路由变化时，根据 path 决定二级栏开关 + active session
  useEffect(() => {
    const match = matchPath("/sessions/:id/:tab", location.pathname);
    if (match) {
      setActiveSessionId(match.params.id);
      setSubbarOpen(true);
      return;
    }
    // 一级 nav 页面：subbar 关闭
    const closeRoutes = ["/", "/credits", "/profile", "/notifications", "/upload"];
    if (closeRoutes.includes(location.pathname)) {
      setSubbarOpen(false);
    }
    // 主播场次列表：subbar 保持当前状态（不动）
    const anchorMatch = matchPath("/anchors/:id", location.pathname);
    if (anchorMatch) {
      setActiveAnchorId(anchorMatch.params.id);
    }
  }, [location.pathname]);

  function handleSelectAnchor(anchorId) {
    setActiveAnchorId(anchorId);
    navigate(`/anchors/${anchorId}`);
    // 注意：不动 subbarOpen（规则 5）
  }

  function handleChangeTab(tab) {
    if (!activeSessionId) return;
    navigate(`/sessions/${activeSessionId}/${tab}`);
  }

  // 当前二级 tab（从路径推）
  const currentMatch = matchPath("/sessions/:id/:tab", location.pathname);
  const activeTab = currentMatch?.params?.tab || "workspace";

  return (
    <div className="app">
      <PrimarySidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
        anchors={anchors}
        selectedAnchorId={activeAnchorId}
        onSelectAnchor={handleSelectAnchor}
      />
      <div className="main-area">
        <TopBar />
        <div className="body">
          <SecondarySidebar
            visible={subbarOpen}
            activeTab={activeTab}
            onChangeTab={handleChangeTab}
          />
          <main className="content">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}
