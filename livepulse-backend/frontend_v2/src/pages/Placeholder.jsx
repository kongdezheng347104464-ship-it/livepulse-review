import { Icon } from "../components/layout/IconSprite";

/**
 * 通用占位页 — 用于尚未实现的视图（W2-W4 会逐个替换）。
 */
export default function Placeholder({ iconName = "settings", title, text, eta }) {
  return (
    <section className="placeholder">
      <div className="ph-icon-wrap">
        <Icon name={iconName} size={28} className="ph-icon-svg" />
      </div>
      <h2 className="ph-title">{title}</h2>
      <p className="ph-text">{text}</p>
      <div className="ph-note">{eta || "frontend_v2 · 待实现"}</div>
    </section>
  );
}
