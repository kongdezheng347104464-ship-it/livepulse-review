//
//  AnchorListView.swift
//  LivePulse
//
//  首页「我的复盘」中控台（登录后主页，MainTabView 首个 tab）。
//  拉 GET /dashboard 一次出全部聚合：上传入口 + 正在分析 + 本月概览
//  + 最近报告 + 常用主播（主播列表降级为下半部模块）。
//

import SwiftUI

@MainActor
struct AnchorListView: View {
    @Environment(AuthStore.self) private var auth

    @State private var dashboard: DashboardResponse?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var showUpload = false

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView().tint(LPColor.accent)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let errorMessage {
                    errorState(errorMessage)
                } else {
                    content
                }
            }
            .background(LPColor.bg)
            .navigationTitle("我的复盘")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { uploadLink }
                ToolbarItem(placement: .topBarTrailing) { accountMenu }
            }
            .navigationDestination(for: Anchor.self) { anchor in
                AnchorSessionsView(anchor: anchor)
            }
            .navigationDestination(for: AnalyzedSession.self) { session in
                SessionWorkspaceLoader(session: session)
            }
        }
        .fullScreenCover(isPresented: $showUpload) {
            VideoPickerView()
        }
        .task { await load() }
    }

    // MARK: - 中控台内容

    @ViewBuilder
    private var content: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: LPSpacing.md) {
                if let d = dashboard {
                    Text(subtitleText(d))
                        .font(.subheadline)
                        .foregroundStyle(LPColor.textMute)

                    greetingCard

                    uploadCard

                    if !d.inProgress.isEmpty {
                        sectionHeader("正在分析")
                        ForEach(d.inProgress) { inProgressRow($0) }
                    }

                    metricsRow(d)

                    if !d.recentReports.isEmpty {
                        sectionHeader("最近报告")
                        ForEach(d.recentReports.prefix(2)) { rep in
                            NavigationLink(value: rep.asSession) { recentRow(rep) }
                                .buttonStyle(.plain)
                        }
                    }

                    if !d.frequentAnchors.isEmpty {
                        sectionHeader("常用主播")
                        ForEach(d.frequentAnchors) { a in
                            NavigationLink(value: a.asAnchor) { AnchorRow(anchor: a.asAnchor) }
                                .buttonStyle(.plain)
                        }
                    }

                    if d.inProgress.isEmpty && d.recentReports.isEmpty && d.frequentAnchors.isEmpty {
                        emptyHome
                    }
                } else {
                    uploadCard
                }
            }
            .padding(16)
        }
        .refreshable { await load() }
    }

    private func subtitleText(_ d: DashboardResponse) -> String {
        if !d.inProgress.isEmpty { return "\(d.inProgress.count) 场正在分析" }
        if d.thisMonth.reviewCount > 0 { return "本月已复盘 \(d.thisMonth.reviewCount) 场" }
        return "上传一场录屏，开始复盘"
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.headline)
            .foregroundStyle(LPColor.text)
            .padding(.top, LPSpacing.xs)
    }

    private func metricsRow(_ d: DashboardResponse) -> some View {
        HStack(spacing: 12) {
            LPMetricCard(value: "\(d.thisMonth.reviewCount)", label: "本月复盘")
            LPMetricCard(
                value: d.thisMonth.avgScore.map { String(Int($0.rounded())) } ?? "—",
                label: "平均分",
                valueColor: LPColor.score(for: d.thisMonth.avgScore)
            )
            LPMetricCard(
                value: "\(d.exportableHighlights)",
                label: "可导出高光",
                valueColor: LPColor.accent
            )
        }
    }

    private func inProgressRow(_ s: InProgressSession) -> some View {
        HStack(spacing: 12) {
            ProgressView().tint(LPColor.accent)
            VStack(alignment: .leading, spacing: 3) {
                Text(s.title ?? s.anchorName ?? "分析中")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(LPColor.text)
                    .lineLimit(1)
                Text("\(s.anchorName.map { "\($0) · " } ?? "")当前阶段：\(s.stage)")
                    .font(.caption)
                    .foregroundStyle(LPColor.textMute)
            }
            Spacer()
        }
        .padding(14)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
    }

    private func recentRow(_ rep: RecentReport) -> some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text(rep.title ?? "未命名场次")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(LPColor.text)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    if let n = rep.anchorName { Text(n) }
                    if let d = ISODate.shortDate(rep.date) { Text("·"); Text(d) }
                }
                .font(.caption)
                .foregroundStyle(LPColor.textMute)
            }
            Spacer()
            ScoreRing(score: rep.compositeScore, label: "综合分", size: 52, lineWidth: 5)
        }
        .padding(14)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
    }

    private var emptyHome: some View {
        VStack(spacing: LPSpacing.sm) {
            Image("EmptyNoRecordings")
                .resizable().scaledToFit()
                .frame(width: 140, height: 140)
            Text("还没有复盘数据")
                .font(.headline).foregroundStyle(LPColor.textMid)
            Text("上传第一场直播录屏开始")
                .font(.subheadline).foregroundStyle(LPColor.textMute)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 32)
    }

    // MARK: - 问候卡片

    private var displayName: String {
        let email = auth.currentUser?.email ?? ""
        let prefix = email.split(separator: "@").first.map(String.init) ?? ""
        return prefix.isEmpty ? "欢迎回来" : prefix
    }

    /// 「你好，」黑 +「用户名」紫;无名时仅「欢迎回来」
    private var greetingText: Text {
        if displayName == "欢迎回来" {
            return Text("欢迎回来").foregroundStyle(LPColor.text)
        }
        return Text("你好，").foregroundStyle(LPColor.text)
            + Text(displayName).foregroundStyle(LPColor.accent)
    }

    // 带小尾巴的对话气泡（贴人物头左侧）
    private var heartBubble: some View {
        Image(systemName: "heart.fill")
            .font(.system(size: 11, weight: .bold))
            .foregroundStyle(Color(hex: 0xFF6B9A))
            .frame(width: 23, height: 23)
            .background(Color.white)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(alignment: .bottomLeading) {
                Triangle()
                    .fill(Color.white)
                    .frame(width: 8, height: 5)
                    .rotationEffect(.degrees(20))
                    .offset(x: 5, y: 3)
            }
            .lpSoftShadow()
    }

    private var greetingCard: some View {
        // 文案留在卡片内(左侧、垂直居中);插画作为「不被裁剪」的 overlay
        // 画在卡片之上、向上溢出卡片顶边(破框)。padding(.top) 预留溢出空间，
        // 不挤到上方副标题。
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                greetingText
                    .font(.title2.weight(.bold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                Text("今天也来复盘一场直播吧")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(LPColor.textMid)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            .frame(width: 150, alignment: .leading)
            Spacer(minLength: 0)
        }
        .padding(.leading, 20)
        .frame(maxWidth: .infinity)
        .frame(height: 130)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.xl, style: .continuous))
        .lpCardShadow()
        .overlay(alignment: .bottomTrailing) {
            Image("HomeGreetingLiveMakeup")
                .resizable()
                .scaledToFit()
                .frame(width: 317)
                .scaleEffect(1.2)
                .offset(x: 42, y: -12)
                .overlay(alignment: .top) {
                    heartBubble.offset(x: 8, y: 12)
                }
        }
        .padding(.top, 8)   // 仅留少量破框空间,删掉多余空隙
    }

    // MARK: - 上传卡 / 工具栏 / 错误态（沿用）

    private var uploadCard: some View {
        Button {
            showUpload = true
        } label: {
            HStack(spacing: 12) {
                Image(systemName: "video.badge.plus")
                    .font(.title3)
                VStack(alignment: .leading, spacing: 2) {
                    Text("上传直播录屏")
                        .font(.subheadline.weight(.semibold))
                    Text("选一段录屏 · 进入复盘工作台")
                        .font(.caption)
                        .opacity(0.85)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.caption)
            }
            .foregroundStyle(.white)
            .padding(18)
            .background(LPGradient.brand)
            .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
            .lpCardShadow()
        }
        .buttonStyle(.plain)
    }

    private var uploadLink: some View {
        Button {
            showUpload = true
        } label: {
            Image(systemName: "plus")
        }
    }

    private var accountMenu: some View {
        Menu {
            if let email = auth.currentUser?.email {
                Text(email)
            }
            Button("退出登录", role: .destructive) { auth.logout() }
        } label: {
            AvatarCircle(
                key: "user/me",
                initial: String((auth.currentUser?.email ?? "U").prefix(1)).uppercased(),
                size: 30
            )
        }
    }

    private func errorState(_ msg: String) -> some View {
        VStack(spacing: LPSpacing.md) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 40))
                .foregroundStyle(LPColor.textFaint)
            Text(msg)
                .font(.subheadline)
                .foregroundStyle(LPColor.textMute)
                .multilineTextAlignment(.center)
            Button("重试") { Task { await load() } }
                .foregroundStyle(LPColor.accent)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - 加载

    private func load() async {
        isLoading = (dashboard == nil)   // 下拉刷新时不显示全屏 loading
        errorMessage = nil
        do {
            dashboard = try await APIClient.shared.get("/dashboard")
        } catch {
            errorMessage = (error as? APIError)?.errorDescription ?? "加载失败"
        }
        isLoading = false
    }
}

// MARK: - 主播行（常用主播复用）

@MainActor
private struct AnchorRow: View {
    let anchor: Anchor

    var body: some View {
        HStack(spacing: 12) {
            AvatarCircle(
                key: "anchors/\(anchor.id)",
                initial: anchor.initial,
                remoteURL: anchor.avatarURL,
                size: 46
            )
            .lpSoftShadow()

            VStack(alignment: .leading, spacing: 3) {
                Text(anchor.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(LPColor.text)
                HStack(spacing: 8) {
                    Text("\(anchor.sessionCount) 场")
                    if let d = anchor.lastSessionText {
                        Text("·")
                        Text("最近 \(d)")
                    }
                    if let p = anchor.platform {
                        Text("·")
                        Text(p)
                    }
                }
                .font(.caption)
                .foregroundStyle(LPColor.textMute)
            }

            Spacer()
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(LPColor.textFaint)
        }
        .padding(15)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
    }
}

// 对话气泡的小尾巴
private struct Triangle: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.minX, y: r.minY))
        p.addLine(to: CGPoint(x: r.maxX, y: r.minY))
        p.addLine(to: CGPoint(x: r.midX, y: r.maxY))
        p.closeSubpath()
        return p
    }
}
