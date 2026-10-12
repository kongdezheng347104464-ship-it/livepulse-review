//
//  RangeAnalysisResultView.swift
//  LivePulse
//
//  AI 选段点评结果 sheet：主播类型 + 一句话判断 + 该保留的亮点 + 诊断 findings。
//

import SwiftUI

/// AI 点评结果内容（不含 NavigationStack，由调用方提供导航容器）。
/// 新点评用 sheet 包 NavigationStack；历史记录用 navigationDestination push。
struct RangeAnalysisResultView: View {
    var loading: Bool = false
    let result: RangeAnalysisResult?
    var errorMessage: String? = nil

    var body: some View {
        Group {
            if loading {
                VStack(spacing: LPSpacing.md) {
                    ProgressView().tint(LPColor.accent)
                    Text("AI 正在点评这段…")
                        .font(.subheadline).foregroundStyle(LPColor.textMute)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let errorMessage {
                VStack(spacing: LPSpacing.sm) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(.system(size: 40)).foregroundStyle(LPColor.textFaint)
                    Text(errorMessage)
                        .font(.subheadline).foregroundStyle(LPColor.textMute)
                        .multilineTextAlignment(.center).padding(.horizontal, 32)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let result {
                content(result)
            }
        }
        .background(LPColor.bg)
        .navigationTitle("AI 点评")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func content(_ r: RangeAnalysisResult) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: LPSpacing.md) {
                // 主播类型 + 一句话判断
                VStack(alignment: .leading, spacing: 8) {
                    if let t = r.streamerType, !t.isEmpty {
                        Text(t)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(LPColor.accentText)
                            .padding(.horizontal, 10).padding(.vertical, 4)
                            .background(LPColor.accentSoft)
                            .clipShape(Capsule())
                    }
                    if let s = r.summary, !s.isEmpty {
                        Text(s)
                            .font(.headline)
                            .foregroundStyle(LPColor.text)
                    }
                }

                // 该保留的亮点
                if let h = r.highlight, !h.isEmpty {
                    card(icon: "star.fill", iconColor: LPColor.scoreMid, title: "该保留", body: h)
                }

                // 诊断 findings
                if r.findings.isEmpty {
                    Text("这段没有明显问题，继续保持 👍")
                        .font(.subheadline)
                        .foregroundStyle(LPColor.textMute)
                        .padding(.top, 4)
                } else {
                    ForEach(r.findings) { f in
                        findingCard(f)
                    }
                }
            }
            .padding(16)
        }
    }

    private func findingCard(_ f: Finding) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                if let d = f.dimension, !d.isEmpty {
                    Text(d)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8).padding(.vertical, 2)
                        .background(LPColor.accent)
                        .clipShape(Capsule())
                }
                if let m = f.moment, !m.isEmpty {
                    Text(m)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(LPColor.textMute)
                }
                Spacer()
            }
            labeledLine("观察", f.observation)
            labeledLine("问题", f.problem)
            labeledLine("行动", f.action, highlight: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.md))
        .overlay(RoundedRectangle(cornerRadius: LPRadius.md).stroke(LPColor.border, lineWidth: 1))
    }

    @ViewBuilder
    private func labeledLine(_ label: String, _ text: String?, highlight: Bool = false) -> some View {
        if let text, !text.isEmpty {
            HStack(alignment: .top, spacing: 6) {
                Text(label)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(highlight ? LPColor.accent : LPColor.textMute)
                    .frame(width: 28, alignment: .leading)
                Text(text)
                    .font(.subheadline)
                    .foregroundStyle(highlight ? LPColor.text : LPColor.textMid)
            }
        }
    }

    private func card(icon: String, iconColor: Color, title: String, body: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: icon).foregroundStyle(iconColor)
                Text(title).font(.caption.weight(.semibold)).foregroundStyle(LPColor.textMid)
            }
            Text(body).font(.subheadline).foregroundStyle(LPColor.text)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.md))
        .overlay(RoundedRectangle(cornerRadius: LPRadius.md).stroke(LPColor.border, lineWidth: 1))
    }
}
