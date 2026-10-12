//
//  AnchorTabView.swift
//  LivePulse
//
//  「主播」Tab：选主播 → 看该主播场次的工作流。
//  顶部搜索 + 主播头像横滑(选中态) + 选中主播摘要卡 + 该主播直播场次列表。
//  数据：/anchors/(主播) + /sessions/?anchor_id=(场次)，复用现有接口。
//
//  注：草图摘要卡的"待查看高光"无按主播聚合接口(纯前端需 N 次请求)，
//  故摘要卡只放真实可得的「场次数 · 平均分」，不伪造该数字。
//

import SwiftUI

@MainActor
struct AnchorTabView: View {
    @State private var anchors: [Anchor] = []
    @State private var selectedAnchor: Anchor?
    @State private var sessions: [AnalyzedSession] = []
    @State private var search = ""

    @State private var isLoadingAnchors = true
    @State private var isLoadingSessions = false
    @State private var errorMessage: String?

    @State private var showUpload = false

    private var filteredAnchors: [Anchor] {
        let q = search.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return anchors }
        return anchors.filter {
            $0.name.localizedCaseInsensitiveContains(q)
            || ($0.platform ?? "").localizedCaseInsensitiveContains(q)
        }
    }

    private var filteredSessions: [AnalyzedSession] {
        let q = search.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return sessions }
        return sessions.filter { ($0.title ?? "").localizedCaseInsensitiveContains(q) }
    }

    var body: some View {
        NavigationStack {
            Group {
                if isLoadingAnchors {
                    ProgressView().tint(LPColor.accent)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let errorMessage {
                    errorState(errorMessage)
                } else if anchors.isEmpty {
                    emptyAnchors
                } else {
                    content
                }
            }
            .background(LPColor.bg)
            .navigationTitle("主播")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showUpload = true } label: { Image(systemName: "plus") }
                }
            }
            .navigationDestination(for: AnalyzedSession.self) { session in
                SessionWorkspaceLoader(session: session)
            }
        }
        .fullScreenCover(isPresented: $showUpload) { VideoPickerView() }
        .task { await loadAnchors() }
        .onChange(of: selectedAnchor?.id) { _, _ in
            Task { await loadSessions() }
        }
    }

    // MARK: - 内容

    private var content: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: LPSpacing.md) {
                Text("选择主播，查看对应直播场次")
                    .font(.subheadline)
                    .foregroundStyle(LPColor.textMute)

                searchBox
                anchorStrip

                if let a = selectedAnchor {
                    summaryCard(a)
                    sessionsSection
                }
            }
            .padding(16)
        }
        .refreshable { await loadAnchors() }
    }

    private var searchBox: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(LPColor.textMute)
            TextField("搜索主播、场次或平台", text: $search)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            if !search.isEmpty {
                Button { search = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(LPColor.textFaint)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpSoftShadow()
    }

    // 主播头像横滑
    private var anchorStrip: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("我的主播").font(.headline).foregroundStyle(LPColor.text)
                Spacer()
                Text("\(anchors.count) 位").font(.caption).foregroundStyle(LPColor.textMute)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 14) {
                    ForEach(filteredAnchors) { anchor in
                        anchorAvatar(anchor)
                    }
                }
                .padding(.vertical, 4)
            }
        }
    }

    private func anchorAvatar(_ anchor: Anchor) -> some View {
        let isSelected = selectedAnchor?.id == anchor.id
        return Button {
            selectedAnchor = anchor
        } label: {
            VStack(spacing: 6) {
                AvatarCircle(
                    key: "anchors/\(anchor.id)",
                    initial: anchor.initial,
                    remoteURL: anchor.avatarURL,
                    size: 56
                )
                .overlay(
                    Circle().stroke(LPColor.accent, lineWidth: isSelected ? 3 : 0)
                )
                .overlay(alignment: .bottomTrailing) {
                    if isSelected {
                        Circle().fill(Color(hex: 0x22C55E))
                            .frame(width: 15, height: 15)
                            .overlay(Circle().stroke(.white, lineWidth: 2))
                    }
                }
                Text(anchor.name)
                    .font(.caption.weight(isSelected ? .semibold : .regular))
                    .foregroundStyle(isSelected ? LPColor.text : LPColor.textMid)
                    .lineLimit(1)
                Text("\(anchor.sessionCount) 场")
                    .font(.caption2)
                    .foregroundStyle(LPColor.textMute)
            }
            .frame(width: 64)
        }
        .buttonStyle(.plain)
    }

    // 选中主播摘要卡
    private func summaryCard(_ anchor: Anchor) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(anchor.name)
                    .font(.headline).foregroundStyle(.white)
                Text("\(anchor.sessionCount) 场 · 平均分 \(avgText)")
                    .font(.caption).foregroundStyle(.white.opacity(0.9))
            }
            Spacer()
            Text(avgText)
                .font(.title2.weight(.bold)).monospacedDigit()
                .foregroundStyle(.white)
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(.white.opacity(0.2))
                .clipShape(Capsule())
        }
        .padding(16)
        .frame(maxWidth: .infinity)
        .background(LPGradient.brand)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
    }

    private var avgText: String {
        let scores = sessions.filter { $0.isAnalyzed }.compactMap { $0.compositeScore }
        guard !scores.isEmpty else { return "—" }
        return String(Int((scores.reduce(0, +) / Double(scores.count)).rounded()))
    }

    // 该主播场次列表
    private var sessionsSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("直播场次").font(.headline).foregroundStyle(LPColor.text)
                .padding(.top, LPSpacing.xs)

            if isLoadingSessions {
                ProgressView().tint(LPColor.accent)
                    .frame(maxWidth: .infinity).padding(.vertical, 24)
            } else if filteredSessions.isEmpty {
                Text(sessions.isEmpty ? "该主播暂无场次记录" : "没有匹配的场次")
                    .font(.subheadline).foregroundStyle(LPColor.textMute)
                    .frame(maxWidth: .infinity).padding(.vertical, 24)
            } else {
                ForEach(filteredSessions) { session in
                    NavigationLink(value: session) { sessionRow(session) }
                        .buttonStyle(.plain)
                }
            }
        }
    }

    private func sessionRow(_ session: AnalyzedSession) -> some View {
        HStack(spacing: 14) {
            Image(systemName: "play.fill")
                .font(.headline)
                .foregroundStyle(.white)
                .frame(width: 48, height: 48)
                .background(LPGradient.primary)
                .clipShape(RoundedRectangle(cornerRadius: LPRadius.md, style: .continuous))

            VStack(alignment: .leading, spacing: 4) {
                Text(session.displayTitle)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(LPColor.text)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    if let d = session.dateText { Text(d) }
                    Text("·")
                    Text(session.isAnalyzed ? "已完成" : session.statusText)
                    if let p = selectedAnchor?.platform { Text("·"); Text(p) }
                }
                .font(.caption)
                .foregroundStyle(LPColor.textMute)
            }

            Spacer()

            if session.isAnalyzed {
                ScoreRing(score: session.compositeScore, label: "综合分", size: 50, lineWidth: 5)
            } else {
                Text("分析中")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(LPColor.accent)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(LPColor.accentSoft)
                    .clipShape(Capsule())
            }
        }
        .padding(14)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
    }

    // MARK: - 状态

    private var emptyAnchors: some View {
        VStack(spacing: LPSpacing.sm) {
            Image("EmptyNoRecordings").resizable().scaledToFit().frame(width: 140, height: 140)
            Text("还没有主播").font(.headline).foregroundStyle(LPColor.textMid)
            Text("上传一场直播录屏，会自动生成主播").font(.subheadline).foregroundStyle(LPColor.textMute)
            Button { showUpload = true } label: { Text("上传录屏") }
                .buttonStyle(LPPrimaryButtonStyle())
                .frame(maxWidth: 200)
                .padding(.top, LPSpacing.sm)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(32)
    }

    private func errorState(_ msg: String) -> some View {
        VStack(spacing: LPSpacing.md) {
            Image(systemName: "wifi.exclamationmark").font(.system(size: 40)).foregroundStyle(LPColor.textFaint)
            Text(msg).font(.subheadline).foregroundStyle(LPColor.textMute).multilineTextAlignment(.center)
            Button("重试") { Task { await loadAnchors() } }.foregroundStyle(LPColor.accent)
        }
        .padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - 加载

    private func loadAnchors() async {
        isLoadingAnchors = anchors.isEmpty
        errorMessage = nil
        do {
            let res: AnchorListResponse = try await APIClient.shared.get(
                "/anchors/", query: ["page_size": "100"]
            )
            anchors = res.items.sorted { $0.sessionCount > $1.sessionCount }
            if selectedAnchor == nil { selectedAnchor = anchors.first }
        } catch {
            errorMessage = (error as? APIError)?.errorDescription ?? "加载失败"
        }
        isLoadingAnchors = false
    }

    private func loadSessions() async {
        guard let anchor = selectedAnchor else { sessions = []; return }
        isLoadingSessions = true
        do {
            let res: SessionListResponse = try await APIClient.shared.get(
                "/sessions/", query: ["page_size": "100", "anchor_id": anchor.id]
            )
            sessions = res.items.sorted {
                ($0.startTime ?? $0.createdAt ?? "") > ($1.startTime ?? $1.createdAt ?? "")
            }
        } catch {
            sessions = []
        }
        isLoadingSessions = false
    }
}
