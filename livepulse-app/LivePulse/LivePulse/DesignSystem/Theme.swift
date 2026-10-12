//
//  Theme.swift
//  LivePulse
//
//  设计系统 v2（2026-06-11）：紫蓝渐变 + 柔和阴影 + 玻璃感白卡 + 圆形评分环。
//  对齐 product-design-board/app-design-full-reference-exact.png。
//
//  兼容策略：保留所有旧 LPColor 命名（现有页面照常编译），但色值/风格切到 v2；
//  新增 LPGradient / LPShadow / .lpCard() / LPPrimaryButtonStyle / ScoreRing 等 v2 组件。
//

import SwiftUI

/// 品牌色板（v2 紫蓝）
enum LPColor {
    /// 主紫 #6366F1
    static let accent = Color(hex: 0x6366F1)
    /// 主紫 hover / 深 #4F46E5
    static let accentHover = Color(hex: 0x4F46E5)
    /// 浅紫底 #EEF0FF（标签底、选中行、头像底）
    static let accentSoft = Color(hex: 0xEEF0FF)
    /// 深紫文字 #4338CA
    static let accentText = Color(hex: 0x4338CA)

    /// 主文字（深靛蓝灰，比纯黑柔和、更品牌化）#1E1B39
    static let text = Color(hex: 0x1E1B39)
    /// 次级文字 #5B5B72
    static let textMid = Color(hex: 0x5B5B72)
    /// 弱文字 #9494A8
    static let textMute = Color(hex: 0x9494A8)
    /// 极弱文字 / 占位图标 #C2C2D2
    static let textFaint = Color(hex: 0xC2C2D2)

    /// 页面背景（浅蓝紫,不是纯灰）#F5F5FC
    static let bg = Color(hex: 0xF5F5FC)
    /// 更浅填充（进度条轨道等）#ECECF6
    static let bgSoft = Color(hex: 0xECECF6)
    /// 卡片白
    static let card = Color.white
    /// 边框（更淡,v2 主要靠阴影区隔）#ECECF3
    static let border = Color(hex: 0xECECF3)

    /// 评分高（绿）
    static let scoreHigh = Color(hex: 0x16A34A)
    /// 评分中（橙）
    static let scoreMid = Color(hex: 0xF59E0B)
    /// 评分低（红）
    static let scoreLow = Color(hex: 0xEF4444)
    /// 评分环轨道（未填充部分）
    static let scoreRingTrack = Color(hex: 0xECECF6)

    /// 按分值取语义色（全 app 单一来源，阈值 75/60；nil/未分析=faint）
    static func score(for value: Double?, analyzed: Bool = true) -> Color {
        guard analyzed, let s = value else { return textFaint }
        if s >= 75 { return scoreHigh }
        if s >= 60 { return scoreMid }
        return scoreLow
    }
}

/// 渐变 token（v2 核心视觉）
enum LPGradient {
    /// 主渐变 #6366F1 → #8B5CF6（主按钮、强调块）
    static let primary = LinearGradient(
        colors: [Color(hex: 0x6366F1), Color(hex: 0x8B5CF6)],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )
    /// 品牌渐变 #4F46E5 → #0EA5E9（靛蓝→蓝,用户卡/hero）
    static let brand = LinearGradient(
        colors: [Color(hex: 0x4F46E5), Color(hex: 0x0EA5E9)],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )
    /// 登录全屏渐变背景（紫,自上而下加深）
    static let authBackground = LinearGradient(
        colors: [Color(hex: 0x7B6CF6), Color(hex: 0x6366F1), Color(hex: 0x4F46E5)],
        startPoint: .top, endPoint: .bottom
    )
    /// 页面浅蓝紫背景渐变
    static let pageBackground = LinearGradient(
        colors: [Color(hex: 0xF7F6FE), Color(hex: 0xF1F1FB)],
        startPoint: .top, endPoint: .bottom
    )
}

/// 圆角规范（v2 卡片更圆）
enum LPRadius {
    static let sm: CGFloat = 8
    static let md: CGFloat = 12
    static let lg: CGFloat = 16
    static let xl: CGFloat = 22
    static let full: CGFloat = 999
}

/// 间距规范（8pt）
enum LPSpacing {
    static let xs: CGFloat = 4
    static let sm: CGFloat = 8
    static let md: CGFloat = 16
    static let lg: CGFloat = 24
    static let xl: CGFloat = 32
}

// MARK: - 阴影

extension View {
    /// 卡片柔和阴影（v2 主要层次手段,替代细边框）
    func lpCardShadow() -> some View {
        shadow(color: Color(hex: 0x6366F1, alpha: 0.10), radius: 16, x: 0, y: 8)
    }
    /// 更轻的阴影（按钮、小浮层）
    func lpSoftShadow() -> some View {
        shadow(color: Color.black.opacity(0.05), radius: 8, x: 0, y: 2)
    }
}

// MARK: - 卡片

/// v2 白卡：圆角 + 柔和阴影（默认无边框,靠阴影抬升）
struct LPCardModifier: ViewModifier {
    var padding: CGFloat = 16
    var radius: CGFloat = LPRadius.lg
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(LPColor.card)
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
            .lpCardShadow()
    }
}

extension View {
    /// 包裹成 v2 白卡
    func lpCard(padding: CGFloat = 16, radius: CGFloat = LPRadius.lg) -> some View {
        modifier(LPCardModifier(padding: padding, radius: radius))
    }
}

// MARK: - 按钮

/// 主按钮：紫色渐变实心 + 白字 + 轻阴影（每屏唯一主操作）
struct LPPrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 15)
            .background(LPGradient.primary)
            .clipShape(RoundedRectangle(cornerRadius: LPRadius.md, style: .continuous))
            .opacity(configuration.isPressed ? 0.85 : 1)
            .lpSoftShadow()
    }
}

/// 次级按钮：浅紫底 + 紫字
struct LPSecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(LPColor.accent)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 13)
            .background(LPColor.accentSoft)
            .clipShape(RoundedRectangle(cornerRadius: LPRadius.md, style: .continuous))
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

// MARK: - 圆形评分环

/// 圆环评分组件（v2 数据可视化核心,复用于场次/洞察/趋势）
struct ScoreRing: View {
    let score: Double?
    var label: String? = nil
    var size: CGFloat = 64
    var lineWidth: CGFloat = 7
    var analyzed: Bool = true

    private var fraction: CGFloat {
        guard analyzed, let s = score else { return 0 }
        return CGFloat(min(max(s, 0), 100) / 100)
    }
    private var color: Color { LPColor.score(for: score, analyzed: analyzed) }

    var body: some View {
        ZStack {
            Circle().stroke(LPColor.scoreRingTrack, lineWidth: lineWidth)
            Circle()
                .trim(from: 0, to: fraction)
                .stroke(color, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                .rotationEffect(.degrees(-90))
            VStack(spacing: 0) {
                Text(score.map { String(Int($0.rounded())) } ?? "—")
                    .font(.system(size: size * 0.32, weight: .bold))
                    .foregroundStyle(color)
                if let label {
                    Text(label)
                        .font(.system(size: size * 0.15, weight: .medium))
                        .foregroundStyle(LPColor.textMute)
                }
            }
        }
        .frame(width: size, height: size)
    }
}

// MARK: - Color hex 便捷初始化

extension Color {
    /// 用 0xRRGGBB 整数创建颜色，例如 Color(hex: 0x6366F1)
    init(hex: UInt, alpha: Double = 1.0) {
        let r = Double((hex >> 16) & 0xFF) / 255.0
        let g = Double((hex >> 8) & 0xFF) / 255.0
        let b = Double(hex & 0xFF) / 255.0
        self.init(.sRGB, red: r, green: g, blue: b, opacity: alpha)
    }
}
