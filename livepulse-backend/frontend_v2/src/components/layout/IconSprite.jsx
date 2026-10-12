/**
 * IconSprite — 全局 SVG symbol 库，mount 一次后所有 <Icon> 都能 <use href="#i-xxx"> 引用。
 * Lucide / Feather 风格，stroke 1.75，linecap round。
 *
 * 用法：
 *   <IconSprite />  // 在 App 根挂一次
 *   <Icon name="users" size={16} />
 */
export function IconSprite() {
  return (
    <svg
      width="0"
      height="0"
      style={{ position: "absolute" }}
      aria-hidden="true"
    >
      <defs>
        <symbol id="i-panel-left" viewBox="0 0 24 24">
          <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" strokeWidth="1.75" fill="none" />
          <line x1="9" y1="3" x2="9" y2="21" stroke="currentColor" strokeWidth="1.75" />
        </symbol>
        <symbol id="i-search" viewBox="0 0 24 24">
          <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.75" fill="none" />
          <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </symbol>
        <symbol id="i-plus" viewBox="0 0 24 24">
          <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </symbol>
        <symbol id="i-bell" viewBox="0 0 24 24">
          <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21a2 2 0 0 0 4 0" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-users" viewBox="0 0 24 24">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          <circle cx="9" cy="7" r="4" stroke="currentColor" strokeWidth="1.75" fill="none" />
          <path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-credit" viewBox="0 0 24 24">
          <rect x="2" y="5" width="20" height="14" rx="2" stroke="currentColor" strokeWidth="1.75" fill="none" />
          <path d="M2 10h20" stroke="currentColor" strokeWidth="1.75" />
        </symbol>
        <symbol id="i-settings" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.75" fill="none" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-mail" viewBox="0 0 24 24">
          <rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.75" fill="none" />
          <path d="m3 7 9 6 9-6" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-logout" viewBox="0 0 24 24">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-chevron-down" viewBox="0 0 24 24">
          <path d="m6 9 6 6 6-6" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-chart" viewBox="0 0 24 24">
          <path d="M3 3v18h18M7 14l4-4 4 4 5-5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-film" viewBox="0 0 24 24">
          <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" strokeWidth="1.75" fill="none" />
          <path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4M3 12h18" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </symbol>
        <symbol id="i-trending" viewBox="0 0 24 24">
          <path d="m3 17 6-6 4 4 8-8M14 7h7v7" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-calendar" viewBox="0 0 24 24">
          <rect x="3" y="5" width="18" height="16" rx="2" stroke="currentColor" strokeWidth="1.5" fill="none" />
          <path d="M16 3v4M8 3v4M3 11h18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </symbol>
        <symbol id="i-clock" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.5" fill="none" />
          <path d="M12 7v5l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </symbol>
        <symbol id="i-arrow-right" viewBox="0 0 24 24">
          <path d="M5 12h14m-6-6 6 6-6 6" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-camera" viewBox="0 0 24 24">
          <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" fill="none" />
          <circle cx="12" cy="13" r="4" stroke="currentColor" strokeWidth="1.5" fill="none" />
        </symbol>
        <symbol id="i-file-text" viewBox="0 0 24 24">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-target" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.75" fill="none" />
          <circle cx="12" cy="12" r="5" stroke="currentColor" strokeWidth="1.75" fill="none" />
          <circle cx="12" cy="12" r="1.5" fill="currentColor" />
        </symbol>
        <symbol id="i-sparkle" viewBox="0 0 24 24">
          <path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5z" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          <path d="M19 14l.7 2.3L22 17l-2.3.7L19 20l-.7-2.3L16 17l2.3-.7z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </symbol>
        <symbol id="i-send" viewBox="0 0 24 24">
          <path d="m3 11 18-8-8 18-2-8-8-2z" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
        </symbol>

        {/* Model logos — 黑白线性，各家辨识特征抽象化（非商标复刻） */}

        {/* DeepSeek — 鲸鱼侧影 */}
        <symbol id="i-m-deepseek" viewBox="0 0 24 24">
          {/* 鲸鱼椭圆身体 */}
          <path d="M3.5 13 C 3.5 9, 8 7.5, 12.5 8 C 17 8, 20.5 10.5, 20 14 C 18 16.5, 13 17, 8.5 16 C 5.5 15, 3.5 14, 3.5 13 Z"
                stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
          {/* 尾鳍 */}
          <path d="M3.5 13 L 1.5 10.5 M 3.5 13 L 1.5 15.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          {/* 眼睛 */}
          <circle cx="16" cy="11" r="0.9" fill="currentColor"/>
          {/* 喷水 */}
          <path d="M15 7 Q 16 5 16 3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" fill="none"/>
          <path d="M18 7.5 Q 19 5.5 19.5 4.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" fill="none"/>
        </symbol>

        {/* ChatGPT — 6 瓣螺旋花（3 椭圆 60° 旋转） */}
        <symbol id="i-m-chatgpt" viewBox="0 0 24 24">
          <g stroke="currentColor" strokeWidth="1.4" fill="none">
            <ellipse cx="12" cy="12" rx="3.5" ry="9"/>
            <ellipse cx="12" cy="12" rx="3.5" ry="9" transform="rotate(60 12 12)"/>
            <ellipse cx="12" cy="12" rx="3.5" ry="9" transform="rotate(120 12 12)"/>
          </g>
        </symbol>

        {/* Claude — 8 瓣放射花（4 椭圆 45° 旋转） */}
        <symbol id="i-m-claude" viewBox="0 0 24 24">
          <g stroke="currentColor" strokeWidth="1.4" fill="none">
            <ellipse cx="12" cy="12" rx="1.6" ry="9"/>
            <ellipse cx="12" cy="12" rx="1.6" ry="9" transform="rotate(45 12 12)"/>
            <ellipse cx="12" cy="12" rx="1.6" ry="9" transform="rotate(90 12 12)"/>
            <ellipse cx="12" cy="12" rx="1.6" ry="9" transform="rotate(135 12 12)"/>
          </g>
        </symbol>

        {/* Gemini — 4 角星菱形 */}
        <symbol id="i-m-gemini" viewBox="0 0 24 24">
          <path d="M12 2 L13.6 10.4 L22 12 L13.6 13.6 L12 22 L10.4 13.6 L2 12 L10.4 10.4 Z"
                stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" fill="none"/>
        </symbol>

        {/* Kimi — 大 K 字 + 右上点 */}
        <symbol id="i-m-kimi" viewBox="0 0 24 24">
          <g stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none">
            <path d="M6.5 5 V 20"/>
            <path d="M6.5 12 L 16 5"/>
            <path d="M6.5 12 L 16 20"/>
          </g>
          <circle cx="18" cy="7" r="1.6" fill="currentColor"/>
        </symbol>
      </defs>
    </svg>
  );
}

/** Inline icon component. <Icon name="users" size={16} className="..." /> */
export function Icon({ name, size = 16, className = "" }) {
  return (
    <svg
      width={size}
      height={size}
      className={className}
      style={{ flexShrink: 0, display: "block" }}
      aria-hidden="true"
    >
      <use href={`#i-${name}`} />
    </svg>
  );
}
