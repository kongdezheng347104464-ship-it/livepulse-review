//
//  RecentSessionsView.swift
//  LivePulse
//
//  "场次" tab（Phase 5）：跨主播的全部场次列表（GET /sessions/ 不带 anchor_id）。
//  点场次进复盘工作台。给底部 Tab 一个全局的"场次"入口。
//

import SwiftUI

struct RecentSessionsView: View {
    @State private var sessions: [AnalyzedSession] = []
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView().tint(LPColor.accent)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let errorMessage {
                    VStack(spacing: LPSpacing.md) {
                        Text(errorMessage)
                            .font(.subheadline).foregroundStyle(LPColor.textMute)
                        Button("重试") { Task { await load() } }
                            .foregroundStyle(LPColor.accent)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if sessions.isEmpty {
                    VStack(spacing: LPSpacing.sm) {
                        Image("EmptyNoRecordings")
                            .resizable().scaledToFit()
                            .frame(width: 140, height: 140)
                        Text("还没有场次记录")
                            .font(.headline).foregroundStyle(LPColor.textMid)
                        Text("上传一场直播录屏开始复盘")
                            .font(.subheadline).foregroundStyle(LPColor.textMute)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            ForEach(sessions) { session in
                                NavigationLink(value: session) {
                                    row(session)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(16)
                    }
                    .refreshable { await load() }
                }
            }
            .background(LPColor.bg)
            .navigationTitle("全部场次")
            .navigationDestination(for: AnalyzedSession.self) { session in
                SessionWorkspaceLoader(session: session)
            }
        }
        .task { await load() }
    }

    private func row(_ session: AnalyzedSession) -> some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                Text(session.displayTitle)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(LPColor.text)
                    .lineLimit(1)
                HStack(spacing: 8) {
                    if let name = session.anchorName {
                        Label(name, systemImage: "person.fill")
                    }
                    if let d = session.dateText {
                        Text("·"); Text(d)
                    }
                    if !session.isAnalyzed {
                        Text(session.statusText).foregroundStyle(LPColor.accent)
                    }
                }
                .font(.caption)
                .foregroundStyle(LPColor.textMute)
            }
            Spacer()
            ScoreRing(
                score: session.compositeScore,
                label: "综合分",
                size: 56,
                lineWidth: 6,
                analyzed: session.isAnalyzed
            )
        }
        .padding(15)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
    }

    private func load() async {
        isLoading = sessions.isEmpty
        errorMessage = nil
        do {
            let res: SessionListResponse = try await APIClient.shared.get(
                "/sessions/", query: ["page_size": "100"]
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
