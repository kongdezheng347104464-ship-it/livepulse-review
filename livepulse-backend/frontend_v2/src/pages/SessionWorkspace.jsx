import { useEffect, useRef, useState, useMemo } from "react";
import { useParams } from "react-router-dom";
import { Spin, Empty, Input, Button, Popconfirm, message, Tabs, Tag, Tooltip, Select } from "antd";
// 官方品牌图标库 — 直接 import Mono（单色）组件
// 不走顶层 default（它会拉 features 层 → 引用 @lobehub/ui，但没装）
import DeepSeek from "@lobehub/icons/es/DeepSeek/components/Mono";
import OpenAI from "@lobehub/icons/es/OpenAI/components/Mono";
import Claude from "@lobehub/icons/es/Claude/components/Mono";
import Gemini from "@lobehub/icons/es/Gemini/components/Mono";
import Kimi from "@lobehub/icons/es/Kimi/components/Mono";
import client from "../api/client";
import { toSafeString } from "../utils/textDecoder";
import { Icon } from "../components/layout/IconSprite";

/**
 * SessionWorkspace (v2) — 简化版回放工作台
 *
 * 范围（W4 Day 1-2）：
 *   - 视频播放（HTML5 video）
 *   - Insights 列表（点击 seek 到时间）
 *   - Notes（列表 + 添加 + 删除，时间戳锚定到当前播放位置）
 *
 * 不做（M2 / 后续 phase）：
 *   - 完整 transcript 分窗口加载
 *   - AI Range Analysis（这是个独立工具，M2 单独做）
 *   - 自定义 timeline 可视化（视频原生进度条够用）
 *   - 键盘快捷键
 *
 * API：
 *   GET /sessions/:id/timeline → {session, video_url, insights, notes, segment_count}
 *   GET/POST/PATCH/DELETE /sessions/:id/notes
 */
export default function SessionWorkspace() {
  const { id: sessionId } = useParams();
  const videoRef = useRef(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [bundle, setBundle] = useState(null);
  const [notes, setNotes] = useState([]);
  const [currentTime, setCurrentTime] = useState(0);
  const [seekTarget, setSeekTarget] = useState(null);
  // 选段（transcript → AI 分析联动）
  const [selectedSegmentIds, setSelectedSegmentIds] = useState([]);
  const [selectedDurationSec, setSelectedDurationSec] = useState(0);
  // 控制切到 AI tab
  const [activeRightTab, setActiveRightTab] = useState("ai");

  // 拉 timeline bundle
  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    client
      .get(`/sessions/${sessionId}/timeline`)
      .then((res) => {
        setBundle(res);
        setNotes(res?.notes || []);
      })
      .catch((err) => setError(err.message || "加载失败"))
      .finally(() => setLoading(false));
  }, [sessionId]);

  // Seek → 跟齐旧版"Scrub-Always-Paused"不变式：
  //   任何外部触发的 seek（点 transcript / timeline marker / 笔记时间戳 / AI moment）
  //   都强制视频暂停。只有用户点 ▶ 才播放。
  useEffect(() => {
    if (seekTarget != null && videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = seekTarget;
      setSeekTarget(null);
    }
  }, [seekTarget]);

  // 监听视频时间变化（节流，每秒最多更新一次）
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    let lastUpdate = 0;
    const onTime = () => {
      const now = Date.now();
      if (now - lastUpdate < 500) return;
      lastUpdate = now;
      setCurrentTime(v.currentTime);
    };
    v.addEventListener("timeupdate", onTime);
    return () => v.removeEventListener("timeupdate", onTime);
  }, [bundle?.video_url]);

  // 全局键盘快捷键：Space 暂停/播放 · ← → seek 5s · Shift+← → seek 15s
  useEffect(() => {
    function onKey(e) {
      const v = videoRef.current;
      if (!v) return;
      // 在 input / textarea / contenteditable 里时禁用快捷键（避免写笔记时空格触发暂停）
      const t = e.target;
      const tag = t?.tagName;
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        t?.isContentEditable
      ) {
        return;
      }

      if (e.code === "Space" || e.key === " ") {
        e.preventDefault();
        if (v.paused) v.play();
        else v.pause();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        const step = e.shiftKey ? 15 : 5;
        v.currentTime = Math.max(0, v.currentTime - step);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        const step = e.shiftKey ? 15 : 5;
        v.currentTime = Math.min(v.duration || 0, v.currentTime + step);
      } else if (e.key === "Escape") {
        setSelectedSegmentIds([]);
        setSelectedDurationSec(0);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [bundle?.video_url]);

  if (loading) {
    return (
      <div style={{ padding: 60, textAlign: "center" }}>
        <Spin size="large" />
      </div>
    );
  }
  if (error || !bundle) {
    return (
      <section className="session-view">
        <Empty description={error || "加载失败"} />
      </section>
    );
  }

  const { session, video_url, insights } = bundle;

  return (
    <section className="workspace-view">
      <div className="workspace-header">
        <div>
          <div className="breadcrumb">
            {toSafeString(session?.anchor_name || "")} · 回放工作台
          </div>
          <h2 className="session-title">{toSafeString(session?.title)}</h2>
          <div className="session-meta">
            {bundle.segment_count} 段话术 · {insights?.length || 0} 个亮点 ·{" "}
            {notes.length} 条笔记
          </div>
        </div>
      </div>

      <div className="workspace-grid">
        {/* Row1 Col1: 视频 + 时间轴 */}
        <div className="ws-video-section">
          {video_url ? (
            <video
              ref={videoRef}
              src={video_url}
              controls
              playsInline
              className="workspace-video"
              onClick={(e) => {
                const v = e.currentTarget;
                if (v.paused) v.play();
                else v.pause();
              }}
            />
          ) : (
            <Empty description="视频源未找到" />
          )}
          <TimelineTrack
            duration={session?.duration_sec || 0}
            currentTime={currentTime}
            insights={insights || []}
            notes={notes || []}
            onSeek={(t) => { setCurrentTime(t); setSeekTarget(t); }}
          />
        </div>

        {/* Row1 Col2: 笔记面板，align-self:stretch 使底部与时间轴底部对齐 */}
        <div className="ws-notes-panel-wrap">
          <NotesPanel
            sessionId={sessionId}
            notes={notes}
            setNotes={setNotes}
            currentTime={currentTime}
            onSeek={(t) => { setCurrentTime(t); setSeekTarget(t); }}
          />
        </div>

        {/* Row2 Col1: 话术面板 */}
        <TranscriptPanel
          sessionId={sessionId}
          currentTime={currentTime}
          duration={session?.duration_sec || 0}
          onSeek={(t) => { setCurrentTime(t); setSeekTarget(t); }}
          selectedSegmentIds={selectedSegmentIds}
          setSelectedSegmentIds={setSelectedSegmentIds}
          onDurationChange={setSelectedDurationSec}
          onRunAI={() => setActiveRightTab("ai")}
        />

        {/* Row2 Col2: AI 功能面板（AI分析 + AI问答 Tab 切换） */}
        <div className="ws-right-panel">
          <Tabs
            activeKey={activeRightTab}
            onChange={setActiveRightTab}
            size="small"
            items={[
              {
                key: "ai",
                label: "AI 分析",
                children: (
                  <AIAnalysisPanel
                    sessionId={sessionId}
                    duration={session?.duration_sec || 0}
                    currentTime={currentTime}
                    selectedSegmentIds={selectedSegmentIds}
                    selectedDurationSec={selectedDurationSec}
                    clearSelection={() => { setSelectedSegmentIds([]); setSelectedDurationSec(0); }}
                    onSeek={(t) => { setCurrentTime(t); setSeekTarget(t); }}
                    setNotes={setNotes}
                  />
                ),
              },
              {
                key: "chat",
                label: "AI 问答",
                children: <ChatPanel />,
              },
            ]}
          />
        </div>
      </div>
    </section>
  );
}

/* ============================================================
   TimelineTrack (W4 跟齐旧版) — drag scrub + cursor handle + 时间 badge
                                + insight ▲ + note ★ markers + tooltip + legend
   ============================================================ */
function TimelineTrack({ duration, currentTime, insights, notes, onSeek }) {
  const barRef = useRef(null);
  // dragTime != null while dragging; commit-on-release seek（避免拖动期间
  // 每帧 seek 触发大量 Range 请求）
  const [dragTime, setDragTime] = useState(null);
  const isDragging = dragTime != null;
  const displayTime = isDragging ? dragTime : currentTime;

  function xToTime(clientX) {
    const rect = barRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return 0;
    const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
    return (x / rect.width) * duration;
  }

  function handleMouseDown(e) {
    if (e.button !== 0) return;
    const t = xToTime(e.clientX);
    setDragTime(t);
    e.preventDefault();
  }

  // 拖动期间挂全局 mousemove + mouseup，松手 commit seek
  useEffect(() => {
    if (!isDragging) return;
    const onMove = (e) => setDragTime(xToTime(e.clientX));
    const onUp = () => {
      const t = dragTime;
      setDragTime(null);
      onSeek(t);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDragging, dragTime, duration, onSeek]);

  if (!duration) {
    return (
      <div className="ws-timeline">
        <div className="ws-timeline-empty">等待视频时长加载…</div>
      </div>
    );
  }

  const cursorPct = (displayTime / duration) * 100;

  return (
    <div className="ws-timeline">
      <div className="ws-timeline-label">
        <span>时间轴</span>
        <span className="ws-timeline-counts">
          {insights.length} 个亮点 · {notes.length} 条笔记
        </span>
      </div>

      {/* 两端时间标签 */}
      <div className="ws-timeline-times">
        <span>{fmtTime(0)}</span>
        <span>{fmtTime(duration)}</span>
      </div>

      <div
        ref={barRef}
        className={`ws-timeline-bar ${isDragging ? "dragging" : ""}`}
        onMouseDown={handleMouseDown}
      >
        {/* Insight markers — top */}
        {insights.map((ins, i) => {
          const t = ins.start_sec || 0;
          if (t > duration) return null;
          const tip = (
            <span style={{ whiteSpace: "pre-line", fontSize: 12 }}>
              {fmtTime(t)} · {scriptTypeLabel(ins.script_type)}
              {ins.score != null ? ` · 分 ${Math.round(ins.score)}` : ""}
              {"\n"}
              {toSafeString(ins.preview || "")}
            </span>
          );
          return (
            <Tooltip key={`ins-${i}`} title={tip} placement="top">
              <div
                className="ws-marker ws-marker-insight"
                style={{ left: `${(t / duration) * 100}%` }}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onSeek(t);
                }}
              >
                ▲
              </div>
            </Tooltip>
          );
        })}

        {/* Note markers — bottom */}
        {notes.map((n) => {
          const t = n.timestamp_sec || 0;
          if (t > duration) return null;
          const txt = toSafeString(n.note_text);
          const tip = (
            <span style={{ whiteSpace: "pre-line", fontSize: 12 }}>
              {fmtTime(t)} ·{" "}
              {txt.slice(0, 80)}
              {txt.length > 80 ? "…" : ""}
            </span>
          );
          return (
            <Tooltip key={`note-${n.id}`} title={tip} placement="bottom">
              <div
                className="ws-marker ws-marker-note"
                style={{ left: `${(t / duration) * 100}%` }}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onSeek(t);
                }}
              >
                ★
              </div>
            </Tooltip>
          );
        })}

        {/* Cursor vertical line */}
        <div
          className="ws-timeline-cursor-line"
          style={{ left: `${cursorPct}%` }}
        />
        {/* Cursor circular handle */}
        <div
          className={`ws-timeline-cursor-handle ${isDragging ? "dragging" : ""}`}
          style={{ left: `${cursorPct}%` }}
        />
        {/* Time badge above cursor */}
        <div
          className="ws-timeline-badge"
          style={{ left: `${cursorPct}%` }}
        >
          {fmtTime(displayTime)}
        </div>
      </div>

      {/* Legend */}
      <div className="ws-timeline-legend">
        <span className="legend-insight">▲ 高分话术</span>
        <span className="legend-note">★ 笔记</span>
        <span className="legend-hint">点击 / 拖动 时间轴跳转</span>
      </div>
    </div>
  );
}

/* ============================================================
   TranscriptPanel — 懒加载 transcript 窗口 + 当前段高亮 + 选段触发 AI
   ============================================================ */

const SEL_MAX = 60;                 // 选段上限（跟 backend Pydantic 一致）

function TranscriptPanel({
  sessionId,
  currentTime,
  duration,
  onSeek,
  selectedSegmentIds,
  setSelectedSegmentIds,
  onDurationChange,
  onRunAI,
}) {
  const [segments, setSegments] = useState([]);
  const [loading, setLoading] = useState(false);
  const listRef = useRef(null);
  const lastAnchorRef = useRef(null);

  // 一次性加载全部话术，支持用户自由滚动浏览全场内容
  useEffect(() => {
    if (!sessionId || !duration) return;
    let cancelled = false;
    setLoading(true);
    client
      .get(`/sessions/${sessionId}/transcript`, {
        params: { start_sec: 0, end_sec: Math.ceil(duration) },
      })
      .then((res) => {
        if (cancelled) return;
        setSegments(res?.segments || []);
      })
      .catch(() => {
        if (!cancelled) setSegments([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [sessionId, duration]);

  // auto-follow：视频播放时列表自动跟随当前段
  // 用户手动滚动后暂停 3s，之后恢复跟随
  const [autoFollow, setAutoFollow] = useState(true);
  const autoFollowTimerRef = useRef(null);
  // 标记"正在程序化滚动"，防止 onScroll 误判为用户手动操作
  const programmaticScrollRef = useRef(false);

  useEffect(() => {
    if (!autoFollow) return;
    const root = listRef.current;
    if (!root) return;
    const active = root.querySelector(".ws-tx-row.active");
    if (!active) return;
    const offsetTop = active.offsetTop - root.clientHeight / 2 + active.clientHeight / 2;
    programmaticScrollRef.current = true;
    root.scrollTo({ top: Math.max(0, offsetTop), behavior: "smooth" });
    // 给浏览器一帧时间完成滚动再解锁，防止 onScroll 误触发
    setTimeout(() => { programmaticScrollRef.current = false; }, 100);
  }, [currentTime, segments.length, autoFollow]);

  function handleListScroll() {
    if (programmaticScrollRef.current) return;
    // 用户手动滚动：暂停 auto-follow 3s
    setAutoFollow(false);
    if (autoFollowTimerRef.current) clearTimeout(autoFollowTimerRef.current);
    autoFollowTimerRef.current = setTimeout(() => setAutoFollow(true), 3000);
  }

  function toggleSelect(seg, ev) {
    const id = seg.id;
    if (!id) return;
    const isSel = selectedSegmentIds.includes(id);

    if (ev.shiftKey && lastAnchorRef.current) {
      // 范围选：取 lastAnchor 到当前 seg 之间所有段
      const anchorIdx = segments.findIndex((s) => s.id === lastAnchorRef.current);
      const curIdx = segments.findIndex((s) => s.id === id);
      if (anchorIdx !== -1 && curIdx !== -1) {
        const [lo, hi] = anchorIdx < curIdx ? [anchorIdx, curIdx] : [curIdx, anchorIdx];
        const range = segments.slice(lo, hi + 1).map((s) => s.id);
        const merged = Array.from(new Set([...selectedSegmentIds, ...range])).slice(0, SEL_MAX);
        setSelectedSegmentIds(merged);
        if (merged.length === SEL_MAX) message.warning(`最多 ${SEL_MAX} 段`);
        return;
      }
    }

    if (ev.metaKey || ev.ctrlKey) {
      // Cmd/Ctrl 多选 toggle
      lastAnchorRef.current = id;
      if (isSel) {
        setSelectedSegmentIds(selectedSegmentIds.filter((x) => x !== id));
      } else {
        if (selectedSegmentIds.length >= SEL_MAX) {
          message.warning(`最多 ${SEL_MAX} 段`);
          return;
        }
        setSelectedSegmentIds([...selectedSegmentIds, id]);
      }
      return;
    }

    // 普通单击：seek 视频 + 单选
    lastAnchorRef.current = id;
    onSeek(seg.start_sec || 0);
    setSelectedSegmentIds(isSel ? [] : [id]);
  }

  // 当前段索引（含 currentTime 的段）
  const currentSegIdx = segments.findIndex(
    (s) => currentTime >= (s.start_sec || 0) && currentTime <= (s.end_sec || 0)
  );

  // 选段总时长（跨段累加），同步回父组件供 AI 面板显示
  const selectedDurationSec = segments.reduce(
    (acc, s) =>
      selectedSegmentIds.includes(s.id)
        ? acc + ((s.end_sec || 0) - (s.start_sec || 0))
        : acc,
    0
  );
  useEffect(() => { onDurationChange?.(selectedDurationSec); }, [selectedDurationSec]);

  // 手动回到当前段：立即恢复 auto-follow + smooth 滚到当前段
  function scrollToCurrent() {
    if (autoFollowTimerRef.current) clearTimeout(autoFollowTimerRef.current);
    setAutoFollow(true);
    const root = listRef.current;
    if (!root) return;
    const active = root.querySelector(".ws-tx-row.active");
    if (active) {
      const offsetTop = active.offsetTop - root.clientHeight / 2 + active.clientHeight / 2;
      programmaticScrollRef.current = true;
      root.scrollTo({ top: Math.max(0, offsetTop), behavior: "smooth" });
      setTimeout(() => { programmaticScrollRef.current = false; }, 100);
    }
  }


  return (
    <div className="ws-transcript-wrap">
      {/* Header: label + 当前时间 + 回到当前 */}
      <div className="ws-tx-head">
        <span className="ws-tx-label">
          <Icon name="file-text" size={14} />
          <span>主播话术</span>
        </span>
        <span className="ws-tx-current">
          当前 <strong>{fmtTime(currentTime)}</strong>
          {loading && <span className="ws-tx-loading"> · 载入中…</span>}
        </span>
        <button
          className="ws-tx-back-btn"
          onClick={scrollToCurrent}
          disabled={currentSegIdx < 0}
          title="滚动到当前播放段"
        >
          <Icon name="target" size={12} />
          <span>回到当前</span>
        </button>
      </div>


      <div className="ws-tx-list" ref={listRef} onScroll={handleListScroll}>
        {segments.length === 0 && !loading ? (
          <div className="empty-mini" style={{ padding: 20 }}>
            该时段无话术
          </div>
        ) : (
          segments.map((seg) => {
            const isActive =
              currentTime >= (seg.start_sec || 0) &&
              currentTime < (seg.end_sec || 0);
            const isSelected = selectedSegmentIds.includes(seg.id);
            return (
              <div
                key={seg.id}
                className={`ws-tx-row ${isActive ? "active" : ""} ${isSelected ? "selected" : ""}`}
                onClick={(e) => toggleSelect(seg, e)}
              >
                <span className="ws-tx-time">
                  {isActive && <span className="ws-tx-prefix-cur">▶ </span>}
                  {isSelected && !isActive && (
                    <span className="ws-tx-prefix-sel">✓ </span>
                  )}
                  {fmtTime(seg.start_sec)}
                </span>
                <span className="ws-tx-type" data-type={seg.script_type}>
                  {scriptTypeLabel(seg.script_type)}
                </span>
                <span className="ws-tx-text">{toSafeString(seg.text)}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

/* ============================================================
   Notes panel — list + add + edit + delete
   ============================================================ */

function NotesPanel({ sessionId, notes, setNotes, currentTime, onSeek }) {
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [anchorTime, setAnchorTime] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState("");
  // 展开态：null = 全部折叠，否则为展开的 note id
  const [expandedId, setExpandedId] = useState(null);

  // 用户清空 textarea 时释放 anchor
  function handleDraftChange(e) {
    const v = e.target.value;
    if (v && anchorTime == null) {
      setAnchorTime(currentTime);
    } else if (!v && anchorTime != null) {
      setAnchorTime(null);
    }
    setDraft(v);
  }

  async function handleAdd() {
    if (!draft.trim()) return;
    const saveAt = anchorTime != null ? anchorTime : currentTime;
    setSaving(true);
    try {
      const created = await client.post(`/sessions/${sessionId}/notes`, {
        timestamp_sec: Math.floor(saveAt),
        note_text: draft.trim(),
      });
      setNotes((prev) => [...prev, created]);
      setDraft("");
      setAnchorTime(null);
      message.success(`已记录 @ ${fmtTime(saveAt)}`);
    } catch (e) {
      message.error(e.message || "添加失败");
    } finally {
      setSaving(false);
    }
  }

  // 显示用：mid-write 显示 anchorTime（"凝固"在开始打字的位置），否则 currentTime
  const displayTime = anchorTime != null ? anchorTime : currentTime;

  async function handleDelete(noteId) {
    try {
      await client.delete(`/sessions/${sessionId}/notes/${noteId}`);
      setNotes((prev) => prev.filter((n) => n.id !== noteId));
      message.success("已删除");
    } catch (e) {
      message.error(e.message || "删除失败");
    }
  }

  function startEdit(note, e) {
    e?.stopPropagation();
    setEditingId(note.id);
    setEditDraft(note.note_text);
  }

  function cancelEdit(e) {
    e?.stopPropagation();
    setEditingId(null);
    setEditDraft("");
  }

  async function saveEdit(noteId, e) {
    e?.stopPropagation();
    const trimmed = editDraft.trim();
    if (!trimmed) { message.warning("笔记不能为空"); return; }
    const original = notes.find((n) => n.id === noteId)?.note_text;
    if (trimmed === original) { setEditingId(null); return; }
    try {
      const updated = await client.patch(`/sessions/${sessionId}/notes/${noteId}`, {
        note_text: trimmed,
      });
      setNotes((prev) => prev.map((n) => (n.id === noteId ? updated : n)));
      setEditingId(null);
      message.success("已更新");
    } catch (e) {
      message.error(e.message || "保存失败");
    }
  }

  const sortedNotes = useMemo(
    () =>
      [...notes].sort(
        (a, b) => (a.timestamp_sec || 0) - (b.timestamp_sec || 0)
      ),
    [notes]
  );

  return (
    <div className="ws-notes-col">
      {/* 面板顶栏：标题左 + 保存按钮右上角 */}
      <div className="ws-notes-topbar">
        <span className="ws-notes-title">复盘笔记 ({notes.length})</span>
        <Button
          type="primary"
          onClick={handleAdd}
          loading={saving}
          disabled={!draft.trim()}
          size="small"
          style={{
            background: "var(--accent)",
            borderColor: "var(--accent)",
            color: "#fff",
            fontSize: 11,
            fontWeight: 500,
          }}
        >
          {draft.trim() ? `保存到 ${fmtTime(displayTime)}` : "保存笔记"}
        </Button>
      </div>

      {/* 输入区：时间指示 + textarea */}
      <div className="ws-notes-input">
        <div className="ws-notes-input-time">
          <Icon name="clock" size={12} />
          <span>{anchorTime != null ? "锚定" : "当前"} {fmtTime(displayTime)}</span>
        </div>
        <Input.TextArea
          value={draft}
          onChange={handleDraftChange}
          placeholder="记一笔（按 Cmd+Enter 提交）..."
          autoSize={{ minRows: 5, maxRows: 10 }}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              handleAdd();
            }
          }}
        />
      </div>

      {/* 笔记列表 */}
      {sortedNotes.length === 0 ? (
        <div className="empty-mini" style={{ marginTop: 12 }}>还没有笔记</div>
      ) : (
        <div className="ws-notes-list">
          {sortedNotes.map((n) => {
            const isExpanded = expandedId === n.id;
            const isEditing = editingId === n.id;
            const preview = toSafeString(n.note_text);
            const previewText = preview.length > 55
              ? preview.slice(0, 55) + "…"
              : preview;

            function toggleExpand(e) {
              e?.stopPropagation();
              if (isEditing) { cancelEdit(); }
              setExpandedId(isExpanded ? null : n.id);
            }

            return (
              <div
                key={n.id}
                className={`ws-note-item ${isExpanded ? "ws-note-expanded" : ""}`}
              >
                {/* 头部：时间戳 + 展开/折叠 + 操作按钮 */}
                <div className="ws-note-head">
                  <button
                    className="ws-note-time"
                    onClick={() => onSeek(n.timestamp_sec || 0)}
                    title="跳到该时刻"
                  >
                    {fmtTime(n.timestamp_sec)}
                  </button>
                  <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
                    {isEditing ? (
                      <>
                        <button className="ws-note-del" style={{ color: "var(--accent)" }}
                          onClick={(e) => saveEdit(n.id, e)} title="保存 (Cmd+Enter)">✓</button>
                        <button className="ws-note-del"
                          onClick={cancelEdit} title="取消 (Esc)">×</button>
                      </>
                    ) : isExpanded ? (
                      <>
                        <button className="ws-note-del" onClick={(e) => startEdit(n, e)}
                          title="编辑" style={{ fontSize: 11 }}>✎</button>
                        <Popconfirm title="删除该笔记？" okText="删除" cancelText="取消"
                          okButtonProps={{ danger: true }} onConfirm={() => handleDelete(n.id)}>
                          <button className="ws-note-del">×</button>
                        </Popconfirm>
                        <button className="ws-note-del" onClick={toggleExpand} title="折叠">▲</button>
                      </>
                    ) : (
                      <button className="ws-note-del" onClick={toggleExpand} title="展开">▼</button>
                    )}
                  </span>
                </div>

                {/* 内容区：折叠时显示摘要，展开时显示全文或编辑框 */}
                {isEditing ? (
                  <Input.TextArea
                    value={editDraft}
                    onChange={(e) => setEditDraft(e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    autoSize={{ minRows: 2 }}
                    autoFocus
                    onKeyDown={(e) => {
                      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                        e.preventDefault(); saveEdit(n.id, e);
                      } else if (e.key === "Escape") {
                        e.preventDefault(); cancelEdit(e);
                      }
                    }}
                    style={{ fontSize: 12, marginTop: 4 }}
                  />
                ) : isExpanded ? (
                  <div className="ws-note-text">{toSafeString(n.note_text)}</div>
                ) : (
                  <div className="ws-note-preview" onClick={toggleExpand}>
                    {previewText}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ============================================================
   AIAnalysisPanel — 选段 → 跑 Claude → 显示意图自适应分析
   ============================================================ */
// 模型选项：claude 是当前唯一可用，其他都是 M2 待接入
// 这里 UI 真实呈现"可选但灰" — 比假装能用诚实
const MODEL_OPTIONS = [
  { value: "claude",   label: "Claude (Anthropic)",  disabled: false, hint: "默认，本场可用" },
  { value: "chatgpt",  label: "ChatGPT (OpenAI)",    disabled: true,  hint: "敬请期待 · M2 接入" },
  { value: "gemini",   label: "Gemini (Google)",     disabled: true,  hint: "敬请期待 · M2 接入" },
  { value: "deepseek", label: "DeepSeek",            disabled: true,  hint: "敬请期待 · M2 接入" },
  { value: "kimi",     label: "Kimi (Moonshot)",     disabled: true,  hint: "敬请期待 · M2 接入" },
  { value: "qwen",     label: "通义千问 (Qwen)",     disabled: true,  hint: "敬请期待 · M2 接入" },
];

function fmtDuration(sec) {
  const s = Math.round(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}分${r}秒` : `${r}秒`;
}

function AIAnalysisPanel({
  sessionId,
  duration,
  currentTime,
  selectedSegmentIds = [],
  selectedDurationSec = 0,
  clearSelection,
  onSeek,
  setNotes,
}) {
  const [running, setRunning] = useState(false);
  const [analyses, setAnalyses] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [model, setModel] = useState("claude");

  // 拉历史
  useEffect(() => {
    if (!sessionId) return;
    client
      .get(`/sessions/${sessionId}/range-analyses`)
      .then((res) => {
        const items = res?.items || [];
        setAnalyses(items);
        if (items.length > 0 && !selectedId) setSelectedId(items[0].id);
      })
      .catch(() => setAnalyses([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  async function handleRun() {
    if (selectedSegmentIds.length === 0) {
      message.warning("请先在左侧 transcript 选段（单击/Shift+Click/Cmd+Click）");
      return;
    }
    setRunning(true);
    try {
      const created = await client.post(
        `/sessions/${sessionId}/range-analysis`,
        // model 字段为 M2 多模型支持预留，当前 backend 忽略该字段（只用 Claude）
        { segment_ids: selectedSegmentIds, model },
        { timeout: 60000 }
      );
      message.success("AI 分析完成");
      setAnalyses((prev) => [created, ...prev]);
      setSelectedId(created.id);
      clearSelection && clearSelection();
    } catch (e) {
      message.error(`AI 分析失败: ${e.message}`);
    } finally {
      setRunning(false);
    }
  }

  const selected = analyses.find((a) => a.id === selectedId);

  return (
    <div className="ws-ai-panel">
      {/* Run new analysis */}
      <div className="ws-ai-input">
        <div className="ws-ai-input-label">
          运行新分析
          {selectedSegmentIds.length > 0 && (
            <span className="ws-ai-sel-count">
              已选 {selectedSegmentIds.length} 段 · {fmtDuration(selectedDurationSec)}
            </span>
          )}
        </div>
        {selectedSegmentIds.length === 0 ? (
          <div className="ws-ai-empty-hint">
            在左侧 transcript 中：
            <br />· 单击选 1 段
            <br />· Shift+Click 选范围
            <br />· Cmd/Ctrl+Click 多选
          </div>
        ) : (
          <>
            {/* Model selector — 默认 Claude，其他暂未接入 */}
            <div className="ws-ai-model-row">
              <label className="ws-ai-model-label">模型</label>
              <Select
                size="small"
                value={model}
                onChange={setModel}
                style={{ flex: 1 }}
                optionLabelProp="label"
                options={MODEL_OPTIONS.map((m) => ({
                  value: m.value,
                  disabled: m.disabled,
                  label: m.label,
                  title: m.hint,
                }))}
                optionRender={(opt) => {
                  const meta = MODEL_OPTIONS.find((m) => m.value === opt.value);
                  return (
                    <div className="ws-ai-model-opt">
                      <span>{meta?.label}</span>
                      <span
                        className={
                          meta?.disabled ? "ws-ai-model-soon" : "ws-ai-model-ok"
                        }
                      >
                        {meta?.disabled ? "敬请期待" : "可用"}
                      </span>
                    </div>
                  );
                }}
              />
            </div>
            <Button
              type="primary"
              loading={running}
              onClick={handleRun}
              size="small"
              style={{
                width: "100%",
                background: "var(--accent)",
                borderColor: "var(--accent)",
                marginTop: 6,
                fontSize: 11,
                fontWeight: 500,
              }}
            >
              {running ? "AI 思考中（约 20s）..." : `分析这 ${selectedSegmentIds.length} 段`}
            </Button>
          </>
        )}
      </div>

      {/* History selector */}
      {analyses.length > 0 && (
        <div className="ws-ai-history">
          <div className="ws-ai-history-label">历史分析 ({analyses.length})</div>
          <div className="ws-ai-history-list">
            {analyses.map((a) => (
              <button
                key={a.id}
                className={`ws-ai-history-item ${selectedId === a.id ? "active" : ""}`}
                onClick={() => setSelectedId(a.id)}
              >
                <span className="ws-ai-history-time">
                  {fmtTime(a.range_start_sec)} – {fmtTime(a.range_end_sec)}
                </span>
                {a.result?.streamer_type && (
                  <Tag color="purple" style={{ fontSize: 10 }}>
                    {a.result.streamer_type}
                  </Tag>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Selected result */}
      {selected ? (
        <AIResultView
          analysis={selected}
          onSeek={onSeek}
          onSaveAsNote={async () => {
            const summary = selected?.result?.summary;
            if (!summary) {
              message.warning("该分析没有可保存的 summary 文本");
              return;
            }
            const anchor = selected.range_start_sec ?? 0;
            try {
              const created = await client.post(`/sessions/${sessionId}/notes`, {
                timestamp_sec: Math.floor(anchor),
                note_text: `[AI 摘要] ${toSafeString(summary)}`,
              });
              setNotes && setNotes((prev) => [...prev, created]);
              message.success(`已保存为笔记 @ ${fmtTime(anchor)}`);
            } catch (e) {
              message.error(e.message || "保存失败");
            }
          }}
        />
      ) : (
        <div className="empty-mini" style={{ marginTop: 16 }}>
          运行一次 AI 分析后，结果显示在此处
        </div>
      )}
    </div>
  );
}

function AIResultView({ analysis, onSeek, onSaveAsNote }) {
  const result = analysis?.result;
  if (!result || typeof result !== "object") {
    return <div className="empty-mini">结果为空</div>;
  }
  const canSave = !!result.summary;
  return (
    <div className="ws-ai-result">
      {/* 顶栏：主播类型 + 另存为笔记 */}
      <div className="ws-ai-result-head">
        {result.streamer_type ? (
          <div className="ws-ai-type">
            主播类型：<Tag color="purple">{result.streamer_type}</Tag>
          </div>
        ) : (
          <div />
        )}
        <Tooltip title={canSave ? "把 summary 保存为笔记，挂在选段开头" : "该分析无 summary 可保存"}>
          <button
            className="ws-ai-save-note"
            onClick={onSaveAsNote}
            disabled={!canSave}
          >
            <Icon name="plus" size={11} />
            <span>另存为笔记</span>
          </button>
        </Tooltip>
      </div>
      {result.highlight && (
        <div className="ws-ai-highlight">
          <div className="ws-ai-block-label">🌟 亮点</div>
          <div className="ws-ai-block-text">{toSafeString(result.highlight)}</div>
        </div>
      )}
      {result.summary && (
        <div className="ws-ai-summary">
          <div className="ws-ai-block-label">总结</div>
          <div className="ws-ai-block-text">{toSafeString(result.summary)}</div>
        </div>
      )}
      {Array.isArray(result.findings) && result.findings.length > 0 && (
        <div className="ws-ai-findings">
          <div className="ws-ai-block-label">改进建议</div>
          {result.findings.map((f, i) => (
            <div key={i} className="ws-ai-finding">
              <div className="ws-ai-finding-head">
                <span className="ws-ai-finding-dim">
                  {toSafeString(f.dimension)}
                </span>
                {f.moment && (
                  <button
                    className="ws-ai-finding-time"
                    onClick={() => {
                      // 解析 mm:ss 跳转
                      const m = String(f.moment).match(/(\d+):(\d{2})/);
                      if (m && onSeek) {
                        onSeek(parseInt(m[1]) * 60 + parseInt(m[2]));
                      }
                    }}
                    title="跳到该时刻"
                  >
                    @ {f.moment}
                  </button>
                )}
              </div>
              {f.observation && (
                <div className="ws-ai-finding-row">
                  <span className="ws-ai-finding-label">观察：</span>
                  {toSafeString(f.observation)}
                </div>
              )}
              {f.problem && (
                <div className="ws-ai-finding-row">
                  <span className="ws-ai-finding-label">问题：</span>
                  {toSafeString(f.problem)}
                </div>
              )}
              {f.action && (
                <div className="ws-ai-finding-action">
                  <span className="opt-action-tag">行动</span>
                  {toSafeString(f.action)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ============================================================
   ChatPanel — AI 问答（左侧黑色模型 logo 侧栏 + 右侧聊天）
   ============================================================ */

// 顺序按 owner 提供的截图：DeepSeek / ChatGPT / Claude / Gemini / Kimi
// Logo 用 @lobehub/icons 官方矢量 Mono 版（黑白）
const CHAT_MODELS = [
  { id: "deepseek", Logo: DeepSeek, name: "DeepSeek", subtitle: "DeepSeek" },
  { id: "chatgpt",  Logo: OpenAI,   name: "ChatGPT",  subtitle: "OpenAI" },
  { id: "claude",   Logo: Claude,   name: "Claude",   subtitle: "Anthropic" },
  { id: "gemini",   Logo: Gemini,   name: "Gemini",   subtitle: "Google" },
  { id: "kimi",     Logo: Kimi,     name: "Kimi",     subtitle: "Moonshot" },
];

function ChatPanel() {
  const [activeModel, setActiveModel] = useState("deepseek");
  // 每个模型独立对话记忆（切模型不丢消息）
  const [conversations, setConversations] = useState(() => {
    const init = {};
    CHAT_MODELS.forEach((m) => (init[m.id] = []));
    return init;
  });
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef(null);

  const meta = CHAT_MODELS.find((m) => m.id === activeModel);
  const messages = conversations[activeModel] || [];

  // 自动滚到底
  useEffect(() => {
    const root = listRef.current;
    if (root) root.scrollTop = root.scrollHeight;
  }, [messages.length, activeModel]);

  async function handleSend() {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    const userMsg = { role: "user", text, at: Date.now() };
    setConversations((prev) => ({
      ...prev,
      [activeModel]: [...(prev[activeModel] || []), userMsg],
    }));
    setDraft("");

    // Mock 回复（backend 暂无 chat endpoint，M2 接入）
    await new Promise((r) => setTimeout(r, 700));
    const botMsg = {
      role: "bot",
      text: `（${meta?.name} 聊天接口尚未接入，当前是 mock 回复 · M2 上线后可正常对话）\n\n你说：${text}`,
      at: Date.now(),
    };
    setConversations((prev) => ({
      ...prev,
      [activeModel]: [...(prev[activeModel] || []), botMsg],
    }));
    setSending(false);
  }

  function clearChat() {
    setConversations((prev) => ({ ...prev, [activeModel]: [] }));
  }

  return (
    <div className="ws-chat">
      {/* 左侧灰色 logo 侧栏 — 只有 5 个模型，无其他按钮 */}
      <div className="ws-chat-sidebar">
        {CHAT_MODELS.map((m) => {
          const LogoComp = m.Logo;
          return (
            <Tooltip key={m.id} title={`${m.name} · ${m.subtitle}`} placement="right">
              <button
                className={`ws-chat-model-btn ${activeModel === m.id ? "active" : ""}`}
                onClick={() => setActiveModel(m.id)}
              >
                <LogoComp size={20} />
              </button>
            </Tooltip>
          );
        })}
      </div>

      {/* 右侧聊天区 — 干净，无 header */}
      <div className="ws-chat-main">
        <div className="ws-chat-list" ref={listRef}>
          {messages.length === 0 ? (
            <div className="ws-chat-empty">
              <div className="ws-chat-empty-icon">
                {meta?.Logo ? <meta.Logo size={32} /> : <Icon name="sparkle" size={32} />}
              </div>
              <div className="ws-chat-empty-title">
                开始与 {meta?.name} 对话
              </div>
              <div className="ws-chat-empty-hint">
                试试问：「这场直播节奏怎么改？」「转化率怎么提升？」
              </div>
            </div>
          ) : (
            messages.map((m, i) => (
              <div key={i} className={`ws-chat-msg msg-${m.role}`}>
                <div className="ws-chat-bubble">{m.text}</div>
              </div>
            ))
          )}
        </div>

        <div className="ws-chat-input">
          <Input.TextArea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={`和 ${meta?.name} 聊聊...（Cmd+Enter 发送）`}
            autoSize={{ minRows: 1, maxRows: 4 }}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                handleSend();
              }
            }}
          />
          <button
            className="ws-chat-send"
            onClick={handleSend}
            disabled={!draft.trim() || sending}
          >
            <Icon name="send" size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------- utils ---------- */
function fmtTime(sec) {
  if (sec == null || isNaN(sec)) return "00:00";
  const total = Math.max(0, Math.floor(sec));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function scriptTypeLabel(t) {
  return (
    {
      teaching: "教学",
      product_intro: "产品",
      promotion: "促销",
      interaction: "互动",
      chitchat: "闲聊",
    }[t] || t || "—"
  );
}
