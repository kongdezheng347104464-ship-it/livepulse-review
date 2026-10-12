//
//  RangeAnalysisHistorySheet.swift
//  LivePulse
//
//  本场次的历史 AI 点评列表。点一条 → 重看完整点评结果。
//

import SwiftUI

struct RangeAnalysisHistorySheet: View {
    let records: [RangeAnalysisRecord]
    let onClose: () -> Void

    var body: some View {
        NavigationStack {
            Group {
                if records.isEmpty {
                    VStack(spacing: LPSpacing.sm) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 40)).foregroundStyle(LPColor.textFaint)
                        Text("还没有 AI 点评记录")
                            .font(.subheadline).foregroundStyle(LPColor.textMute)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ScrollView {
                        LazyVStack(spacing: 10) {
                            ForEach(records) { rec in
                                NavigationLink(value: rec) {
                                    row(rec)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(16)
                    }
                }
            }
            .background(LPColor.bg)
            .navigationTitle("AI 点评记录")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: RangeAnalysisRecord.self) { rec in
                RangeAnalysisResultView(result: rec.result)
            }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { onClose() }
                }
            }
        }
    }

    private func row(_ rec: RangeAnalysisRecord) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(rec.rangeText)
                    .font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(LPColor.accent)
                if let t = rec.result.streamerType, !t.isEmpty {
                    Text(t)
                        .font(.caption2)
                        .foregroundStyle(LPColor.textMute)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.caption2).foregroundStyle(LPColor.textFaint)
            }
            if let s = rec.result.summary, !s.isEmpty {
                Text(s)
                    .font(.subheadline)
                    .foregroundStyle(LPColor.text)
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(12)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.md))
        .overlay(RoundedRectangle(cornerRadius: LPRadius.md).stroke(LPColor.border, lineWidth: 1))
    }
}
