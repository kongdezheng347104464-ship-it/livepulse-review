import { Icon } from "./IconSprite";

/**
 * SecondarySidebar — 二级侧栏（仅 session 视图下显示）。
 *
 * 三个 tab：分析报告 / 回放工作台 / 趋势看板
 *
 * 显示逻辑由 AppShell 通过 visible 控制（不在这里决定）。
 */
export default function SecondarySidebar({ visible, activeTab, onChangeTab }) {
  if (!visible) return null;

  return (
    <aside className="sidebar-secondary">
      <div className="sub-title">复盘工具</div>
      <button
        className={`sub-nav ${activeTab === "workspace" ? "active" : ""}`}
        onClick={() => onChangeTab("workspace")}
      >
        <Icon name="film" size={14} className="sub-nav-icon" />
        <span>回放工作台</span>
      </button>
      <button
        className={`sub-nav ${activeTab === "report" ? "active" : ""}`}
        onClick={() => onChangeTab("report")}
      >
        <Icon name="file-text" size={14} className="sub-nav-icon" />
        <span>分析报告</span>
      </button>
      <button
        className={`sub-nav ${activeTab === "trend" ? "active" : ""}`}
        onClick={() => onChangeTab("trend")}
      >
        <Icon name="trending" size={14} className="sub-nav-icon" />
        <span>趋势看板</span>
      </button>

      <div className="sub-footnote">
        点击左侧任意按钮
        <br />
        即可结束本场复盘
      </div>
    </aside>
  );
}
