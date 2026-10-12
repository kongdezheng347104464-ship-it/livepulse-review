//
//  TrendView.swift
//  LivePulse
//
//  跨场进步追踪折线图：综合分 + 互动/转化/内容 跨场次趋势。
//  差异化核心 —— 让主播看到"自己在进步"，不是看行业数据。
//

import SwiftUI
import Charts

struct TrendView: View {
    let anchorId: String
    let anchorName: String

    @State private var trend: AnchorTrend?
    @State private var isLoading = true
    @State private var errorMessage: String?

    /// 图表用的扁平点（item × 指标）
    private struct ChartPoint: Identifiable {
        let id = UUID()
        let index: Int        // 第几场（x 轴顺序）
        let label: String
        let metric: String    // 综合/互动/转化/内容
        let value: Double
    }

    private var chartPoints: [ChartPoint] {
        guard let items = trend?.items else { return [] }
        var out: [ChartPoint] = []
        for (i, p) in items.enumerated() {
            if let v = p.composite { out.append(.init(index: i, label: p.label, metric: "综合", value: v)) }
            if let v = p.engagement { out.append(.init(index: i, label: p.label, metric: "互动", value: v)) }
            if let v = p.conversion { out.append(.init(index: i, label: p.label, metric: "转化", value: v)) }
            if let v = p.content { out.append(.init(index: i, label: p.label, metric: "内容", value: v)) }
        }
        return out
    }

    private let metricColors: KeyValuePairs<String, Color> = [
        "综合": LPColor.text,
        "互动": LPColor.accent,
        "转化": LPColor.scoreMid,
        "内容": LPColor.scoreHigh,
    ]

    var body: some View {
        Group {
            if isLoading {
                ProgressView().tint(LPColor.accent)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let errorMessage {
                state(icon: "exclamationmark.triangle", text: errorMessage)
            } else if let trend, trend.items.count < 2 {
                state(icon: "chart.xyaxis.line",
                      text: "至少需要 2 场有效记录才能看趋势\n（满 \(trend.minDurationMin ?? 10) 分钟的场次）")
            } else if let trend {
                content(trend)
            }
        }
        .background(LPColor.bg)
        .navigationTitle("跨场进步")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private func content(_ trend: AnchorTrend) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: LPSpacing.md) {
                Text("\(trend.totalSessions) 场 · 综合分变化")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(LPColor.text)

                chart
                    .frame(height: 260)
                    .lpCard()

                latestScores(trend)

                latestDelta(trend)
            }
            .padding(16)
        }
    }

    private var chart: some View {
        Chart(chartPoints) { p in
            LineMark(
                x: .value("场次", p.index),
                y: .value("分数", p.value)
            )
            .foregroundStyle(by: .value("指标", p.metric))
            .symbol(by: .value("指标", p.metric))

            PointMark(
                x: .value("场次", p.index),
                y: .value("分数", p.value)
            )
            .foregroundStyle(by: .value("指标", p.metric))
        }
        .chartForegroundStyleScale(metricColors)
        .chartYScale(domain: 0...100)
        .chartXAxis {
            AxisMarks(values: .automatic) { value in
                AxisValueLabel {
                    if let i = value.as(Int.self),
                       let items = trend?.items, i >= 0, i < items.count {
                        Text(items[i].label).font(.caption2)
                    }
                }
            }
        }
        .chartLegend(position: .bottom)
    }

    /// 最新一场 vs 上一场综合分差
    @ViewBuilder
    private func latestDelta(_ trend: AnchorTrend) -> some View {
        let comps = trend.items.compactMap { $0.composite }
        if comps.count >= 2 {
            let delta = comps[comps.count - 1] - comps[comps.count - 2]
            let up = delta >= 0
            HStack(spacing: 6) {
                Image(systemName: up ? "arrow.up.right" : "arrow.down.right")
                Text("最新一场综合分 \(up ? "上升" : "下降") \(String(format: "%.1f", abs(delta))) 分")
            }
            .font(.subheadline.weight(.medium))
            .foregroundStyle(up ? LPColor.scoreHigh : LPColor.scoreLow)
            .lpCard()
        }
    }

    /// 最新一场三分（环形）
    @ViewBuilder
    private func latestScores(_ trend: AnchorTrend) -> some View {
        if let last = trend.items.last {
            HStack(spacing: 12) {
                miniRing("互动", last.engagement)
                miniRing("转化", last.conversion)
                miniRing("内容", last.content)
            }
        }
    }

    private func miniRing(_ label: String, _ value: Double?) -> some View {
        VStack(spacing: 8) {
            ScoreRing(score: value, size: 54, lineWidth: 6)
            Text(label).font(.caption).foregroundStyle(LPColor.textMute)
        }
        .frame(maxWidth: .infinity)
        .lpCard(padding: 14)
    }

    private func state(icon: String, text: String) -> some View {
        VStack(spacing: LPSpacing.sm) {
            Image(systemName: icon).font(.system(size: 40)).foregroundStyle(LPColor.textFaint)
            Text(text)
                .font(.subheadline).foregroundStyle(LPColor.textMute)
                .multilineTextAlignment(.center).padding(.horizontal, 32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        do {
            trend = try await APIClient.shared.get("/anchors/\(anchorId)/trend")
        } catch {
            errorMessage = (error as? APIError)?.errorDescription ?? "加载失败"
        }
        isLoading = false
    }
}
