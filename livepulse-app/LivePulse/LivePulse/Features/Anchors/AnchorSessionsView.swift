//
//  AnchorSessionsView.swift
//  LivePulse
//
//  主播场次分析表。拉 backend GET /sessions/?anchor_id=...，
//  列表显示每场的综合分 + 三分 + 时长 + 日期 + 状态。
//

import SwiftUI

@MainActor
struct AnchorSessionsView: View {
    let anchor: Anchor

    @State private var sessions: [AnalyzedSession] = []
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var filter: SessionFilter = .all

    enum SessionFilter: String, CaseIterable, Identifiable {
        case all = "全部", analyzed = "已分析", processing = "分析中"
        var id: String { rawValue }
    }

    private var filteredSessions: [AnalyzedSession] {
        switch filter {
        case .all: return sessions
        case .analyzed: return sessions.filter { $0.isAnalyzed }
        case .processing: return sessions.filter { !$0.isAnalyzed }
        }
    }

    var body: some View {
        Group {
            if isLoading {
                ProgressView().tint(LPColor.accent)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let errorMessage {
                VStack(spacing: LPSpacing.md) {
                    Text(errorMessage)
                        .font(.subheadline)
                        .foregroundStyle(LPColor.textMute)
                    Button("重试") { Task { await load() } }
                        .foregroundStyle(LPColor.accent)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                content
            }
        }
        .background(LPColor.bg)
        .navigationTitle(anchor.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink {
                    TrendView(anchorId: anchor.id, anchorName: anchor.name)
                } label: {
                    Image(systemName: "chart.xyaxis.line")
                }
            }
        }
        .navigationDestination(for: AnalyzedSession.self) { session in
            // 对齐 web：点场次进复盘工作台（流式播后端视频 + 记笔记）
            SessionWorkspaceLoader(session: session)
        }
        .task { await load() }
    }

    private var anchorHeader: some View {
        VStack(spacing: LPSpacing.sm) {
            EditableAvatar(
                key: "anchors/\(anchor.id)",
                initial: anchor.initial,
                remoteURL: anchor.avatarURL,
                size: 84
            )
            Text(anchor.name)
                .font(.headline)
                .foregroundStyle(LPColor.text)
            Text("\(sessions.count) 场记录")
                .font(.caption)
                .foregroundStyle(LPColor.textMute)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, LPSpacing.sm)
    }

    private var content: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                anchorHeader

                Picker("", selection: $filter) {
                    ForEach(SessionFilter.allCases) { f in
                        Text(f.rawValue).tag(f)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.bottom, 2)

                if filteredSessions.isEmpty {
                    Text(sessions.isEmpty ? "该主播暂无场次记录" : "没有\(filter.rawValue)的场次")
                        .font(.subheadline)
                        .foregroundStyle(LPColor.textMute)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 48)
                } else {
                    ForEach(filteredSessions) { session in
                        NavigationLink(value: session) {
                            SessionRow(session: session)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(16)
        }
        .refreshable { await load() }
    }

    private func load() async {
        isLoading = sessions.isEmpty
        errorMessage = nil
        do {
            let res: SessionListResponse = try await APIClient.shared.get(
                "/sessions/", query: ["page_size": "100", "anchor_id": anchor.id]
            )
            sessions = res.items.sorted {
                ($0.startTime ?? $0.createdAt ?? "") > ($1.startTime ?? $1.createdAt ?? "")
            }
        } catch {
            errorMessage = (error as? APIError)?.errorDescription ?? "加载失败"
        }
        isLoading = false
    }
}

// MARK: - 场次行（分析表的一行）

private struct SessionRow: View {
    let session: AnalyzedSession

    var body: some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                Text(session.displayTitle)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(LPColor.text)
                    .lineLimit(1)

                HStack(spacing: 8) {
                    if let d = session.dateText { Label(d, systemImage: "calendar") }
                    if let dur = session.durationText { Label(dur, systemImage: "clock") }
                    if !session.isAnalyzed {
                        Text(session.statusText)
                            .foregroundStyle(LPColor.accent)
                    }
                }
                .font(.caption)
                .foregroundStyle(LPColor.textMute)

                // 三分小标签
                if session.isAnalyzed, let s = session.scores {
                    HStack(spacing: 6) {
                        scorePill("互动", s.engagement)
                        scorePill("转化", s.conversion)
                        scorePill("内容", s.content)
                    }
                }
            }

            Spacer()

            // 综合分环
            ScoreRing(
                score: session.compositeScore,
                label: "综合分",
                size: 58,
                lineWidth: 6,
                analyzed: session.isAnalyzed
            )
        }
        .padding(15)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
    }

    private func scorePill(_ label: String, _ value: Double?) -> some View {
        Text("\(label) \(value.map { String(Int($0.rounded())) } ?? "—")")
            .font(.system(size: 10, weight: .medium))
            .foregroundStyle(LPColor.textMid)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(LPColor.bgFor(label))
            .clipShape(Capsule())
    }
}

private extension LPColor {
    static func bgFor(_ label: String) -> Color {
        switch label {
        case "互动": return Color(hex: 0xEEF2FF)
        case "转化": return Color(hex: 0xFFF7ED)
        default: return Color(hex: 0xF0FDF4)
        }
    }
}
