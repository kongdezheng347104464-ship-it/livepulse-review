import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Modal, Form, Input, Select, message } from "antd";
import useAuthStore from "../../store/authStore";
import { Icon } from "./IconSprite";
import client from "../../api/client";

/**
 * PrimarySidebar — 一级侧栏。全高 (top to bottom)。
 *
 * 行为契约（owner 钉死的 7 条）：
 *   - 点 toggle → 整栏折叠/展开（折叠时 logo 替代 toggle，hover 互换）
 *   - 点 "主播列表" toggle → 仅手风琴展开/收起，不影响二级栏
 *   - 点某个主播 → 跳 anchor sessions（二级栏保持当前状态）
 *   - 点 积分/个人/系统/退出 → 跳对应页 + 二级栏隐藏（由 AppShell 处理）
 *   - 折叠状态 hover nav → 右侧弹 tooltip 显示功能名
 */
export default function PrimarySidebar({
  collapsed,
  onToggleCollapse,
  anchors,
  selectedAnchorId,
  onSelectAnchor,
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const logout = useAuthStore((s) => s.logout);
  const [anchorsExpanded, setAnchorsExpanded] = useState(true);

  // 新建主播 Modal
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  async function handleCreate(values) {
    const name = values.name.trim();
    setCreating(true);
    try {
      const anchor = await client.post("/anchors/", {
        name,
        platform: values.platform || "douyin",
      });
      // 通知 AppShell 追加新主播，与 anchor:renamed 机制保持一致
      window.dispatchEvent(
        new CustomEvent("anchor:created", { detail: { anchor } })
      );
      message.success(`主播「${anchor.name}」已创建`);
      setCreateOpen(false);
      form.resetFields();
      navigate(`/anchors/${anchor.id}`);
    } catch (e) {
      const detail = e.response?.data?.detail;
      if (typeof detail === "object" && detail?.error?.includes("canonical name")) {
        message.error("已有同名主播，请换一个名称");
      } else {
        message.error(e.message || "创建失败，请重试");
      }
    } finally {
      setCreating(false);
    }
  }

  function handleDeleteAnchor(anchor) {
    const sessionText = anchor.session_count > 0
      ? `将同时删除其 ${anchor.session_count} 场分析记录，`
      : "";
    Modal.confirm({
      title: `删除主播「${anchor.name}」`,
      content: `${sessionText}此操作不可恢复。`,
      okText: "确认删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await client.delete(`/anchors/${anchor.id}`);
          window.dispatchEvent(
            new CustomEvent("anchor:deleted", { detail: { id: anchor.id } })
          );
          message.success(`主播「${anchor.name}」已删除`);
        } catch (e) {
          message.error(e.message || "删除失败");
        }
      },
    });
  }

  function handleToggleAnchors() {
    // 折叠状态下点主播列表 → 自动展开侧栏 + 展开主播列表（顺手）
    if (collapsed) {
      onToggleCollapse();
      setAnchorsExpanded(true);
    } else {
      setAnchorsExpanded((v) => !v);
    }
  }

  function handleLogout() {
    Modal.confirm({
      title: "确定退出登录吗？",
      okText: "退出",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: () => {
        logout();
        navigate("/login", { replace: true });
      },
    });
  }

  // 判断当前是否处于某个一级 nav 页
  const isViewActive = (path) => location.pathname === path;

  return (
    <>
    <aside className={`sidebar-primary ${collapsed ? "collapsed" : ""}`}>
      <div className="sidebar-head">
        <div className="brand" onClick={() => navigate("/")}>
          <img src="/logo.png" alt="LivePulse" className="brand-logo" />
          <span className="brand-name">LivePulse</span>
        </div>
        <button
          className="btn-sidebar-toggle"
          onClick={onToggleCollapse}
          title="折叠 / 展开侧栏"
        >
          <Icon name="panel-left" size={18} />
        </button>
      </div>

      <div className="sidebar-nav">
        <div className="sidebar-section">
          <button
            className={`nav-collapsible ${anchorsExpanded ? "expanded" : ""}`}
            onClick={handleToggleAnchors}
            data-tooltip="主播列表"
          >
            <Icon name="users" size={16} className="nav-icon" />
            <span className="nav-label">主播列表</span>
            <Icon name="chevron-down" size={12} className="chevron" />
          </button>
          {anchorsExpanded && !collapsed && (
            <div className="anchor-list expanded">
              {anchors.map((a) => (
                <div key={a.id} className="anchor-item-wrap">
                  <button
                    className={`anchor-item ${selectedAnchorId === a.id ? "active" : ""}`}
                    onClick={() => onSelectAnchor(a.id)}
                  >
                    <div className="anchor-avatar">{a.name.charAt(0)}</div>
                    <span>{a.name}</span>
                  </button>
                  <button
                    className="anchor-del-btn"
                    onClick={(e) => { e.stopPropagation(); handleDeleteAnchor(a); }}
                    title="删除主播"
                  >×</button>
                </div>
              ))}
              <button
                className="anchor-item anchor-add"
                onClick={() => setCreateOpen(true)}
              >
                <div className="anchor-avatar anchor-avatar-add">+</div>
                <span>新建主播</span>
              </button>
            </div>
          )}
        </div>

        <button
          className={`nav-item ${isViewActive("/credits") ? "active" : ""}`}
          onClick={() => navigate("/credits")}
          data-tooltip="积分套餐"
        >
          <Icon name="credit" size={16} className="nav-icon" />
          <span className="nav-label">积分套餐</span>
        </button>
        <button
          className={`nav-item ${isViewActive("/profile") ? "active" : ""}`}
          onClick={() => navigate("/profile")}
          data-tooltip="个人信息"
        >
          <Icon name="settings" size={16} className="nav-icon" />
          <span className="nav-label">个人信息</span>
        </button>
        <button
          className={`nav-item ${isViewActive("/notifications") ? "active" : ""}`}
          onClick={() => navigate("/notifications")}
          data-tooltip="系统提示"
        >
          <Icon name="mail" size={16} className="nav-icon" />
          <span className="nav-label">系统提示</span>
        </button>
      </div>

      <div className="sidebar-footer">
        <button
          className="nav-item nav-logout"
          onClick={handleLogout}
          data-tooltip="退出登录"
        >
          <Icon name="logout" size={16} className="nav-icon" />
          <span className="nav-label">退出登录</span>
        </button>
        <div className="footer-text">v0.1 · frontend_v2</div>
      </div>
    </aside>

    <Modal

      title="新建主播"
      open={createOpen}
      onCancel={() => { setCreateOpen(false); form.resetFields(); }}
      onOk={() => form.submit()}
      okText="创建"
      cancelText="取消"
      confirmLoading={creating}
      okButtonProps={{ style: { background: "var(--accent)", borderColor: "var(--accent)" } }}
      destroyOnClose
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={handleCreate}
        style={{ marginTop: 12 }}
      >
        <Form.Item
          name="name"
          label="主播名称"
          rules={[
            { required: true, message: "请输入主播名称" },
            { max: 100, message: "最多 100 个字符" },
            { whitespace: true, message: "名称不能为纯空格" },
          ]}
        >
          <Input placeholder="例：教化妆的阿野" autoFocus maxLength={100} />
        </Form.Item>
        <Form.Item
          name="platform"
          label="平台"
          initialValue="douyin"
        >
          <Select>
            <Select.Option value="douyin">抖音</Select.Option>
            <Select.Option value="kuaishou">快手</Select.Option>
            <Select.Option value="taobao">淘宝直播</Select.Option>
          </Select>
        </Form.Item>
      </Form>
    </Modal>
    </>
  );
}
