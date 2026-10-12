import { useState, useEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { message } from "antd";
import useAuthStore from "../../store/authStore";
import { Icon } from "./IconSprite";
import client from "../../api/client";

/**
 * TopBar — 顶栏，在一级侧栏右侧（不跨越侧栏）。
 * 含搜索框（场次 + 主播）+ 上传按钮 + 通知 + 用户信息。
 *
 * 搜索行为（M1 前端 filter，M2 再考虑后端 search endpoint）：
 *   - 首次 focus 时懒加载 /sessions/ + /anchors/，5min 内不重复拉
 *   - 输入文字 → 模糊匹配 session.title / session.anchor_name / anchor.name
 *   - 下拉分组显示场次 (前 6) + 主播 (前 4)
 *   - 点击场次 → /sessions/:id/report
 *   - 点击主播 → /anchors/:id
 *   - ESC / 点外面 关闭下拉
 */
export default function TopBar() {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const initial = (user?.email || "?").charAt(0).toUpperCase();

  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [anchors, setAnchors] = useState([]);
  const [focusedIndex, setFocusedIndex] = useState(-1);
  const searchRef = useRef(null);

  // 首次 focus 时懒加载（避免每个页面 mount 都拉）
  async function ensureLoaded() {
    if (loaded || loading) return;
    setLoading(true);
    try {
      // 后端 page_size 上限 100；M1 阶段数据量够（owner 自己几十场）
      // M2 数据量上来后改成后端 search endpoint，前端不再全量拉
      const [s, a] = await Promise.all([
        client.get("/sessions/", { params: { page_size: 100 } }),
        client.get("/anchors/", { params: { page_size: 100 } }),
      ]);
      setSessions(s?.items || []);
      setAnchors(a?.items || []);
      setLoaded(true);
    } catch (e) {
      console.error("搜索数据加载失败:", e);
      message.error("搜索数据加载失败");
    } finally {
      setLoading(false);
    }
  }

  // 点外面关闭
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (!searchRef.current?.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // 关闭时重置 focusedIndex
  useEffect(() => { if (!open) setFocusedIndex(-1); }, [open]);

  const { matchedSessions, matchedAnchors } = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return { matchedSessions: [], matchedAnchors: [] };
    const ms = sessions
      .filter(
        (s) =>
          (s.title || "").toLowerCase().includes(q) ||
          (s.anchor_name || "").toLowerCase().includes(q)
      )
      .slice(0, 6);
    const ma = anchors
      .filter((a) => (a.name || "").toLowerCase().includes(q))
      .slice(0, 4);
    return { matchedSessions: ms, matchedAnchors: ma };
  }, [query, sessions, anchors]);

  function goSession(s) {
    setOpen(false);
    setQuery("");
    setFocusedIndex(-1);
    navigate(`/sessions/${s.id}/workspace`);
  }
  function goAnchor(a) {
    setOpen(false);
    setQuery("");
    setFocusedIndex(-1);
    navigate(`/anchors/${a.id}`);
  }

  const q = query.trim();
  const noMatch =
    loaded && q && matchedSessions.length === 0 && matchedAnchors.length === 0;

  return (
    <header className="topbar">
      <div className="search" ref={searchRef}>
        <Icon name="search" size={14} className="search-icon" />
        <input
          type="text"
          placeholder="搜索场次 / 主播..."
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setFocusedIndex(-1);
            setOpen(true);
          }}
          onFocus={() => {
            setOpen(true);
            ensureLoaded();
          }}
          onKeyDown={(e) => {
            const total = matchedSessions.length + matchedAnchors.length;
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setFocusedIndex((i) => Math.min(i + 1, total - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setFocusedIndex((i) => Math.max(i - 1, -1));
            } else if (e.key === "Enter") {
              e.preventDefault();
              if (focusedIndex >= 0 && focusedIndex < matchedSessions.length) {
                goSession(matchedSessions[focusedIndex]);
              } else if (focusedIndex >= matchedSessions.length) {
                goAnchor(matchedAnchors[focusedIndex - matchedSessions.length]);
              }
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />

        {open && (
          <div className="search-popover">
            {loading && <div className="search-empty">加载中…</div>}
            {!loading && !q && loaded && (
              <div className="search-empty">输入场次标题或主播名搜索</div>
            )}
            {!loading && noMatch && (
              <div className="search-empty">无匹配结果</div>
            )}

            {matchedSessions.length > 0 && (
              <div className="search-group">
                <div className="search-group-title">
                  场次 · {matchedSessions.length}
                </div>
                {matchedSessions.map((s, i) => (
                  <button
                    key={s.id}
                    type="button"
                    className={`search-item${focusedIndex === i ? " active" : ""}`}
                    onClick={() => goSession(s)}
                  >
                    <Icon name="chart" size={13} className="search-item-icon" />
                    <span className="search-item-title">
                      {s.title || "（未命名场次）"}
                    </span>
                    {s.anchor_name && (
                      <span className="search-item-sub">{s.anchor_name}</span>
                    )}
                  </button>
                ))}
              </div>
            )}

            {matchedAnchors.length > 0 && (
              <div className="search-group">
                <div className="search-group-title">
                  主播 · {matchedAnchors.length}
                </div>
                {matchedAnchors.map((a, j) => (
                  <button
                    key={a.id}
                    type="button"
                    className={`search-item${focusedIndex === matchedSessions.length + j ? " active" : ""}`}
                    onClick={() => goAnchor(a)}
                  >
                    <Icon name="users" size={13} className="search-item-icon" />
                    <span className="search-item-title">{a.name}</span>
                    {a.session_count != null && (
                      <span className="search-item-sub">
                        {a.session_count} 场
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="top-actions">
        <button
          className="btn-upload"
          title="上传新场次"
          onClick={() => navigate("/upload")}
        >
          <Icon name="plus" size={14} />
          <span>上传场次</span>
        </button>
        <button
          className="btn-icon"
          title="系统提示"
          onClick={() => navigate("/notifications")}
        >
          <Icon name="bell" size={16} />
          <span className="badge">2</span>
        </button>
        <div
          className="user-menu"
          onClick={() => navigate("/profile")}
          role="button"
        >
          <div className="avatar">{initial}</div>
          <div className="user-info">
            <div className="user-name">{user?.email?.split("@")[0] || "User"}</div>
            <div className="user-role">{user?.role === "admin" ? "Owner" : "User"}</div>
          </div>
        </div>
      </div>
    </header>
  );
}
