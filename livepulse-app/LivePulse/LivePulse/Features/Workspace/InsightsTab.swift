//
//  InsightsTab.swift
//  LivePulse
//
//  洞察 Tab：拉 backend 分析报告（综合分 + 三分 + AI 洞察），
//  点洞察跳到视频对应时刻。教练 narrative 的核心展示。
//

import SwiftUI

struct InsightsTab: View {
    /// 后端 session id（= sessionId.uuidString）
    let backendSessionId: String
    /// 点洞察跳转视频
    let onSeek: (Double) -> Void

    @State private var report: AnalysisReport?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var notReady = false

    var body: some View {
        Group {
            if isLoading {
                loadingState
            } else if notReady {
                notReadyState
            } else if let errorMessage {
                messageState(icon: "exclamationmark.triangle", text: errorMessage)
            } else if let report {
                content(report)
            }
        }
        .task { await load() }
    }

    // MARK: - 内容

    private func content(_ report: AnalysisReport) -> some View {
        ScrollView {
            VStack(spacing: LPSpacing.md) {
                if let s = report.summary {
                    scoreCard(s)
                }
                if report.insights.isEmpty {
                    messageState(icon: "sparkles", text: "本场暂无突出洞察")
                        .padding(.top, 40)
                } else {
                    HStack {
                        Text("关键洞察 · 下一步建议")
                            .font(.headline)
                            .foregroundStyle(LPColor.text)
                        Spacer()
                    }
                    .padding(.top, LPSpacing.xs)
                    ForEach(report.insights) { insight in
                        insightCard(insight)
                    }
                }
            }
            .padding(16)
        }
    }

    private func scoreCard(_ s: ReportSummary) -> some View {
        VStack(spacing: LPSpacing.lg) {
            ScoreRing(score: s.overallScore, label: "综合分", size: 120, lineWidth: 11)
            HStack(spacing: 12) {
                miniRing("互动", s.engagementScore)
                miniRing("转化", s.conversionScore)
                miniRing("内容", s.contentScore)
            }
        }
        .lpCard(padding: 20)
    }

    private func miniRing(_ label: String, _ value: Double?) -> some View {
        VStack(spacing: 6) {
            ScoreRing(score: value, size: 64, lineWidth: 6)
            Text(label).font(.caption).foregroundStyle(LPColor.textMute)
        }
        .frame(maxWidth: .infinity)
    }

    private func insightCard(_ insight: ReportInsight) -> some View {
        Button {
            if let s = insight.startSec { onSeek(s) }
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Text(insight.impactLabel)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(impactColor(insight.impact))
                        .clipShape(Capsule())
                    if insight.priority == "high" {
                        Text("重点")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(LPColor.scoreLow)
                    }
                    Spacer()
                    if insight.startSec != nil {
                        HStack(spacing: 3) {
                            Image(systemName: "play.circle")
                            Text("跳转")
                        }
                        .font(.caption2)
                        .foregroundStyle(LPColor.accent)
                    }
                }
                Text(insight.text)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(LPColor.text)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if let claim = insight.claim, claim != insight.text {
                    Text(claim)
                        .font(.caption)
                        .foregroundStyle(LPColor.textMute)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(14)
            .background(LPColor.card)
            .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
            .lpCardShadow()
        }
        .buttonStyle(.plain)
    }

    // MARK: - 状态

    private var loadingState: some View {
        VStack(spacing: LPSpacing.sm) {
            ProgressView().tint(LPColor.accent)
            Text("加载分析报告…").font(.subheadline).foregroundStyle(LPColor.textMute)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var notReadyState: some View {
        messageState(icon: "hourglass", text: "分析还在进行中，完成后这里会显示报告")
    }

    private func messageState(icon: String, text: String) -> some View {
        VStack(spacing: LPSpacing.sm) {
            Image(systemName: icon).font(.system(size: 40)).foregroundStyle(LPColor.textFaint)
            Text(text)
                .font(.subheadline).foregroundStyle(LPColor.textMute)
                .multilineTextAlignment(.center).padding(.horizontal, 32)
            Button("刷新") { Task { await load() } }
                .font(.subheadline).foregroundStyle(LPColor.accent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - 加载

    private func load() async {
        isLoading = true
        notReady = false
        errorMessage = nil
        do {
            let detail: SessionDetailEnvelope = try await APIClient.shared.get(
                "/sessions/\(backendSessionId)"
            )
            if let r = detail.session.analysisSummary?.insightReport {
                report = r
            } else {
                notReady = true   // 还没分析完 / 无报告
            }
        } catch {
            errorMessage = (error as? APIError)?.errorDescription ?? "加载失败"
        }
        isLoading = false
    }

    private func impactColor(_ impact: String?) -> Color {
        switch impact {
        case "conversion": return LPColor.scoreMid
        case "engagement": return LPColor.accent
        case "content": return LPColor.scoreHigh
        default: return LPColor.textMute
        }
    }
}
