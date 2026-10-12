//
//  LPMetricCard.swift
//  LivePulse
//
//  首页中控台的指标卡（本月复盘 / 平均分 / 可导出高光）。
//

import SwiftUI

struct LPMetricCard: View {
    let value: String
    let label: String
    var valueColor: Color = LPColor.text

    var body: some View {
        VStack(spacing: 6) {
            Text(value)
                .font(.system(size: 24, weight: .bold))
                .foregroundStyle(valueColor)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Text(label)
                .font(.caption)
                .foregroundStyle(LPColor.textMute)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 16)
        .padding(.horizontal, 8)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
    }
}
