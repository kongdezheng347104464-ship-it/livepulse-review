import { useEffect, useState, useMemo, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Spin, Empty, message, Modal, Input, Button } from "antd";
import EditOutlined from "@ant-design/icons/EditOutlined";
import DeleteOutlined from "@ant-design/icons/DeleteOutlined";
import CheckOutlined from "@ant-design/icons/CheckOutlined";
import CloseOutlined from "@ant-design/icons/CloseOutlined";
import client from "../api/client";
import { Icon } from "../components/layout/IconSprite";

/**
 * AnchorSessions — 主播的场次列表页。
 *
 * 路由: /anchors/:anchorId
 *
 * 数据策略：
 *   - GET /anchors/ 拿到所有主播信息（用于显示主播名 + 历史评分）
 *   - GET /sessions/?page_size=100 拿到所有 session，前端 filter
 *
 * 卡片交互（对齐旧 frontend SessionList）：
 *   - hover 显示标题旁铅笔 icon + 右上角垃圾桶 icon
 *   - 改名：PATCH /sessions/{id}/title；Enter 保存 / Esc / 点外取消
 *   - 删除：DELETE /sessions/{id}；乐观更新（先 UI 移除，失败再回滚 + 报错）
 */
export default function AnchorSessions() {
  const { anchorId } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [anchor, setAnchor] = useState(null);
  const [allSessions, setAllSessions] = useState([]);

  // 场次改名状态
  const [editingId, setEditingId] = useState(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const editBoxRef = useRef(null);
  const inputRef = useRef(null);

  // 主播名改名状态
  const [editingAnchorName, setEditingAnchorName] = useState(false);
  const [anchorNameDraft, setAnchorNameDraft] = useState("");
  const [savingAnchor, setSavingAnchor] = useState(false);
  const anchorInputRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        // 后端 /sessions/ 支持 anchor_id 过滤，让 DB 直接返回当前主播的场次，
        // 比"全量拉 100 条到前端再 .filter()"快很多（数据量从全量降到 1/5~1/10）
        const [anchorsRes, sessionsRes] = await Promise.all([
          client.get("/anchors/", { params: { page_size: 100 } }),
          client.get("/sessions/", {
            params: { page_size: 100, anchor_id: anchorId },
          }),
        ]);
        if (cancelled) return;
        const found = (anchorsRes?.items || []).find((a) => a.id === anchorId);
        setAnchor(found || null);
        setAllSessions(sessionsRes?.items || []);
      } catch (e) {
        if (!cancelled) message.error(e.message || "加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [anchorId]);

  // 点编辑框外面 → 取消
  useEffect(() => {
    if (!editingId) return;
    const onClick = (e) => {
      if (editBoxRef.current && !editBoxRef.current.contains(e.target)) {
        setEditingId(null);
        setEditingTitle("");
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [editingId]);

  // Filter sessions for this anchor, sort by start_time desc
  const sessions = useMemo(() => {
    return allSessions
      .filter((s) => s.anchor_id === anchorId)
      .sort((a, b) => {
        const ta = a.start_time || a.created_at || "";
        const tb = b.start_time || b.created_at || "";
        return tb.localeCompare(ta);
      });
  }, [allSessions, anchorId]);

  const latestScore = useMemo(() => {
    const analyzed = sessions.filter(
      (s) => s.status === "analyzed" && s.composite_score != null
    );
    return analyzed[0]?.composite_score;
  }, [sessions]);

  function startEdit(item) {
    setEditingId(item.id);
    setEditingTitle(item.title || "");
    setTimeout(() => inputRef.current?.focus({ cursor: "end" }), 30);
  }

  function cancelEdit() {
    setEditingId(null);
    setEditingTitle("");
  }

  function startAnchorEdit() {
    setAnchorNameDraft(anchor.name);
    setEditingAnchorName(true);
    setTimeout(() => anchorInputRef.current?.focus(), 20);
  }

  function cancelAnchorEdit() {
    setEditingAnchorName(false);
    setAnchorNameDraft("");
  }

  async function saveAnchorName() {
    const name = anchorNameDraft.trim();
    if (!name) { message.warning("主播名不能为空"); return; }
    if (name === anchor.name) { cancelAnchorEdit(); return; }
    setSavingAnchor(true);
    try {
      await client.patch(`/anchors/${anchorId}`, { name });
      setAnchor((prev) => ({ ...prev, name }));
      cancelAnchorEdit();
      message.success("主播名已更新");
      window.dispatchEvent(new CustomEvent("anchor:renamed", { detail: { id: anchorId, name } }));
    } catch (e) {
      message.error(e.message || "保存失败");
    } finally {
      setSavingAnchor(false);
    }
  }

  async function saveEdit() {
    const t = editingTitle.trim();
    if (!t) {
      message.warning("标题不能为空");
      return;
    }
    setSaving(true);
    try {
      await client.patch(`/sessions/${editingId}/title`, { title: t });
      setAllSessions((prev) =>
        prev.map((s) => (s.id === editingId ? { ...s, title: t } : s))
      );
      message.success("已保存");
      setEditingId(null);
      setEditingTitle("");
    } catch (e) {
      message.error(e.message || "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  function handleDelete(item) {
    Modal.confirm({
      title: "确认删除",
      content: (
        <div>
          <p style={{ marginBottom: 6 }}>
            确认删除「{item.title || "未命名场次"}」？
          </p>
          <p style={{ color: "var(--text-mute)", fontSize: 12, margin: 0 }}>
            删除后不可恢复，包括所有分析数据和视频文件。
          </p>
        </div>
      ),
      okText: "确认删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        // 乐观更新：先从前端移除
        const snapshot = allSessions;
        setAllSessions((prev) => prev.filter((s) => s.id !== item.id));
        try {
          await client.delete(`/sessions/${item.id}`);
        } catch (e) {
          // 失败 → 回滚 + 报错（守"不要静默吞错"原则）
          setAllSessions(snapshot);
          message.error(e.message || "删除失败，已恢复列表");
        }
      },
    });
  }

  if (loading) {
    return (
      <div style={{ padding: 60, textAlign: "center" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!anchor) {
    return (
      <section className="anchor-view">
        <Empty description="主播不存在或无权限查看" />
      </section>
    );
  }

  return (
    <section className="anchor-view">
      <div className="view-header">
        <div>
          <div className="breadcrumb">主播</div>
          {editingAnchorName ? (
            <div className="anchor-name-edit">
              <Input
                ref={anchorInputRef}
                value={anchorNameDraft}
                onChange={(e) => setAnchorNameDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveAnchorName();
                  else if (e.key === "Escape") cancelAnchorEdit();
                }}
                size="large"
                style={{ fontSize: 22, fontWeight: 700, width: 280 }}
              />
              <Button
                type="primary"
                size="small"
                icon={<CheckOutlined />}
                loading={savingAnchor}
                onClick={saveAnchorName}
                style={{ background: "var(--accent)", borderColor: "var(--accent)" }}
              />
              <Button
                size="small"
                icon={<CloseOutlined />}
                onClick={cancelAnchorEdit}
                disabled={savingAnchor}
              />
            </div>
          ) : (
            <div className="anchor-name-row">
              <h2 className="anchor-title">{anchor.name}</h2>
              <button
                className="anchor-name-edit-btn"
                onClick={startAnchorEdit}
                title="修改主播名"
              >
                <EditOutlined />
              </button>
            </div>
          )}
          <div className="anchor-meta">
            {sessions.length} 场分析记录
            {latestScore != null && ` · 最新评分 ${Math.round(latestScore)}`}
            {anchor.platform && ` · ${anchor.platform}`}
          </div>
        </div>
      </div>

      {sessions.length === 0 ? (
        <Empty description="该主播暂无场次记录" style={{ marginTop: 60 }} />
      ) : (
        <div className="session-list">
          {sessions.map((s) => (
            <SessionCard
              key={s.id}
              session={s}
              isEditing={editingId === s.id}
              editingTitle={editingTitle}
              saving={saving}
              editBoxRef={editingId === s.id ? editBoxRef : null}
              inputRef={editingId === s.id ? inputRef : null}
              onChangeTitle={setEditingTitle}
              onStartEdit={() => startEdit(s)}
              onCancelEdit={cancelEdit}
              onSaveEdit={saveEdit}
              onDelete={() => handleDelete(s)}
              onClick={() => navigate(`/sessions/${s.id}/workspace`)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function SessionCard({
  session,
  isEditing,
  editingTitle,
  saving,
  editBoxRef,
  inputRef,
  onChangeTitle,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onDelete,
  onClick,
}) {
  const date = formatDate(session.start_time || session.created_at);
  const durationMin = session.duration_sec
    ? Math.round(session.duration_sec / 60)
    : null;
  const status = session.status;
  const score = session.composite_score;
  const isAnalyzed = status === "analyzed";

  // 编辑态时整张卡不再触发跳转
  function handleCardClick(e) {
    if (isEditing) return;
    onClick?.();
  }

  return (
    <div
      className={`session-card ${isEditing ? "is-editing" : ""}`}
      onClick={handleCardClick}
    >
      <div className="session-main">
        {isEditing ? (
          <div
            className="session-edit-box"
            ref={editBoxRef}
            onClick={(e) => e.stopPropagation()}
          >
            <Input
              ref={inputRef}
              value={editingTitle}
              onChange={(e) => onChangeTitle(e.target.value)}
              onPressEnter={onSaveEdit}
              onKeyDown={(e) => {
                if (e.key === "Escape") onCancelEdit();
              }}
              size="middle"
              style={{ width: 260, marginRight: 8 }}
              maxLength={200}
            />
            <Button
              type="primary"
              size="small"
              icon={<CheckOutlined />}
              loading={saving}
              onClick={onSaveEdit}
            >
              保存
            </Button>
            <Button
              size="small"
              icon={<CloseOutlined />}
              onClick={onCancelEdit}
              style={{ marginLeft: 4 }}
            />
          </div>
        ) : (
          <div className="session-card-title">
            <span>{session.title || "未命名场次"}</span>
            <button
              type="button"
              className="session-edit-btn"
              title="修改标题"
              onClick={(e) => {
                e.stopPropagation();
                onStartEdit();
              }}
            >
              <EditOutlined />
            </button>
            <button
              type="button"
              className="session-delete-btn"
              title="删除场次"
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
            >
              <DeleteOutlined />
            </button>
          </div>
        )}

        <div className="session-chips">
          {date && (
            <span className="chip">
              <Icon name="calendar" size={12} className="chip-icon" />
              {date}
            </span>
          )}
          {durationMin != null && (
            <span className="chip">
              <Icon name="clock" size={12} className="chip-icon" />
              {durationMin} 分钟
            </span>
          )}
          {!isAnalyzed && (
            <span className={`chip status-${status}`}>
              {statusLabel(status)}
            </span>
          )}
        </div>
      </div>

      <div className="session-score">
        <div className={`session-score-num ${scoreClass(score, isAnalyzed)}`}>
          {isAnalyzed && score != null ? Math.round(score) : "—"}
        </div>
        <div className="session-score-label">综合分</div>
      </div>

      <div className="session-cta">
        <span>{isAnalyzed ? "进入工作台" : statusLabel(status)}</span>
        <span className="session-cta-arrow">
          <Icon name="arrow-right" size={12} />
        </span>
      </div>
    </div>
  );
}

function formatDate(iso) {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  } catch {
    return null;
  }
}

function scoreClass(score, isAnalyzed) {
  if (!isAnalyzed || score == null) return "";
  if (score >= 75) return "high";
  if (score >= 60) return "medium";
  return "low";
}

function statusLabel(status) {
  return (
    {
      uploaded: "待分析",
      processing: "分析中",
      analyzed: "已完成",
      error: "失败",
      failed: "失败",
    }[status] || status
  );
}
