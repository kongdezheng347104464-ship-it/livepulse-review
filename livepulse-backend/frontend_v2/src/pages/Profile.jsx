import { useEffect, useState } from "react";
import { Spin, message, Modal, Input, Empty, Tag } from "antd";
import useAuthStore from "../store/authStore";
import client from "../api/client";
import { Icon } from "../components/layout/IconSprite";

/**
 * Profile (v2 W4 Day 4) — 个人信息 + 邀请码管理（admin 才显示）
 *
 * - 任何用户：显示自己的 email / role / 创建时间
 * - admin：下方加邀请码管理（列表 + 创建）
 * - 改密码暂不做（M2 backend 没 endpoint）
 */
export default function Profile() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  return (
    <section className="profile-view">
      <div className="profile-header">
        <div className="breadcrumb">个人信息</div>
        <h2 className="session-title">账号设置</h2>
      </div>

      <UserInfoCard user={user} />

      {isAdmin && <InvitationsCard />}

      {!isAdmin && (
        <div className="profile-card" style={{ marginTop: 16 }}>
          <div className="card-label">操作</div>
          <div style={{ fontSize: 13, color: "var(--text-mute)", padding: "20px 0" }}>
            修改密码、登录历史等功能将在 M2 阶段开放
          </div>
        </div>
      )}
    </section>
  );
}

function UserInfoCard({ user }) {
  if (!user) return <Spin />;
  return (
    <div className="profile-card">
      <div className="card-label">基本信息</div>
      <div className="profile-info-grid">
        <InfoRow label="邮箱" value={user.email} />
        <InfoRow
          label="角色"
          value={user.role === "admin" ? "Owner" : "User"}
          tag={user.role === "admin" ? "purple" : null}
        />
        <InfoRow label="状态" value={user.is_active ? "正常" : "已停用"} />
        <InfoRow
          label="用户 ID"
          value={user.id}
          mono
        />
      </div>
    </div>
  );
}

function InfoRow({ label, value, tag, mono }) {
  return (
    <div className="profile-info-row">
      <div className="profile-info-label">{label}</div>
      <div
        className="profile-info-value"
        style={{
          fontFamily: mono ? "'SF Mono', Monaco, monospace" : "inherit",
          fontSize: mono ? 12 : 14,
        }}
      >
        {tag ? <Tag color={tag}>{value}</Tag> : value || "—"}
      </div>
    </div>
  );
}

function InvitationsCard() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const res = await client.get("/admin/invitations");
      setItems(res?.items || []);
    } catch (e) {
      message.error(`加载邀请码失败：${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleCreate() {
    Modal.confirm({
      title: "创建邀请码",
      content: "默认有效期 7 天，可在创建后复制 code 发给新用户",
      okText: "创建",
      cancelText: "取消",
      onOk: async () => {
        setCreating(true);
        try {
          const res = await client.post("/admin/invitations", {
            expires_in_days: 7,
          });
          message.success(`已创建：${res?.code || "成功"}`);
          await load();
        } catch (e) {
          message.error(`创建失败：${e.message}`);
        } finally {
          setCreating(false);
        }
      },
    });
  }

  function copyCode(code) {
    navigator.clipboard
      .writeText(code)
      .then(() => message.success(`已复制：${code}`))
      .catch(() => message.error("复制失败，请手动选中"));
  }

  return (
    <div className="profile-card" style={{ marginTop: 16 }}>
      <div className="profile-card-head">
        <div className="card-label" style={{ margin: 0 }}>
          邀请码管理（共 {items.length} 条）
        </div>
        <button
          className="btn-upload"
          onClick={handleCreate}
          disabled={creating}
          style={{ padding: "4px 12px" }}
        >
          <Icon name="plus" size={12} />
          <span>新建邀请码</span>
        </button>
      </div>

      {loading ? (
        <div style={{ padding: 40, textAlign: "center" }}>
          <Spin />
        </div>
      ) : items.length === 0 ? (
        <Empty description="还没有邀请码" />
      ) : (
        <div className="inv-table">
          <div className="inv-row inv-head">
            <span>Code</span>
            <span>状态</span>
            <span>使用者</span>
            <span>创建时间</span>
            <span>过期时间</span>
            <span></span>
          </div>
          {items.map((inv) => (
            <div key={inv.code} className="inv-row">
              <span className="inv-code">{inv.code}</span>
              <span>
                <StatusTag status={inv.status} />
              </span>
              <span className="inv-mute">{inv.used_by_email || "—"}</span>
              <span className="inv-mute">{fmtDate(inv.created_at)}</span>
              <span className="inv-mute">{fmtDate(inv.expires_at) || "永不"}</span>
              <span>
                <button
                  className="inv-copy"
                  onClick={() => copyCode(inv.code)}
                  title="复制"
                >
                  复制
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StatusTag({ status }) {
  const meta = {
    unused: { color: "blue", label: "未使用" },
    used: { color: "green", label: "已使用" },
    expired: { color: "default", label: "已过期" },
  }[status] || { color: "default", label: status };
  return (
    <Tag color={meta.color} style={{ fontSize: 10, padding: "0 6px" }}>
      {meta.label}
    </Tag>
  );
}

function fmtDate(iso) {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  } catch {
    return null;
  }
}
function pad(n) {
  return String(n).padStart(2, "0");
}
