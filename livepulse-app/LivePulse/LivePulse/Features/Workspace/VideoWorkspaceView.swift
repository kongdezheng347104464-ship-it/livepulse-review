//
//  VideoWorkspaceView.swift
//  LivePulse
//
//  复盘工作台（Sprint 4A 骨架）：
//    顶部 播放器 + 自定义时间轴
//    下方 TabView：笔记 / 话术 / 洞察 / 切片
//
//  "上传即开工"核心容器 —— 选完视频立刻能播放 + 记笔记，
//  话术/洞察/切片 tab 是占位（4C/4D + backend 接入后填充）。
//

import SwiftUI
import SwiftData

@MainActor
struct VideoWorkspaceView: View {
    /// 场次 id（本地 ReviewSession.id 或后端 session 的 UUID）。笔记挂这个。
    let sessionId: UUID
    let title: String
    let videoURL: URL

    @State private var playerController: PlayerController
    @State private var selectedTab: WorkspaceTab = .notes
    /// 笔记区是否展开（上滑笔记列表时为 true，播放器缩小让出空间）
    @State private var notesExpanded = false

    // 时间轴上的笔记标记点
    @Query private var notes: [Note]

    // 播放器高度：
    // - 展开态统一 270 → 两种朝向下方内容区高度一致（≈ 记笔记 sheet 高度）
    // - 非展开态：竖屏 480（大）；横屏视频本身就矮，固定 270（再大全是黑边没意义）
    private var playerHeight: CGFloat {
        if notesExpanded {
            return 270
        }
        return playerController.isPortrait ? 480 : 270
    }

    init(sessionId: UUID, title: String, videoURL: URL) {
        self.sessionId = sessionId
        self.title = title
        self.videoURL = videoURL
        _playerController = State(initialValue: PlayerController(url: videoURL))
        let sid = sessionId
        _notes = Query(filter: #Predicate<Note> { $0.sessionId == sid })
    }

    var body: some View {
        VStack(spacing: 0) {
            // 播放器（无系统控件，画面随笔记列表上滑联动缩小）
            PlayerLayerView(player: playerController.player)
                .frame(maxWidth: .infinity)
                .frame(height: playerHeight)
                .background(.black)
                .clipped()
                .overlay(alignment: .topTrailing) {
                    // 缩小态时：右上角放大按钮 + 整块可点恢复
                    if notesExpanded {
                        Button {
                            notesExpanded = false
                        } label: {
                            Image(systemName: "arrow.up.left.and.arrow.down.right")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.white)
                                .padding(8)
                                .background(.black.opacity(0.45))
                                .clipShape(Circle())
                                .padding(8)
                        }
                    }
                }
                .contentShape(Rectangle())
                .onTapGesture {
                    // 缩小态点视频 → 恢复大；正常态点视频 → 播放/暂停
                    if notesExpanded {
                        notesExpanded = false
                    } else {
                        playerController.togglePlay()
                    }
                }
                .animation(.easeInOut(duration: 0.25), value: notesExpanded)

            // 时间轴 + 播放控制
            controls
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(LPColor.card)

            // Tab 选择条
            tabBar

            Divider()

            // Tab 内容（统一拖动监听：所有 tab 上滑都缩小播放器、下滑恢复）
            tabContent
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(LPColor.bg)
                .simultaneousGesture(
                    DragGesture(minimumDistance: 12)
                        .onChanged { value in
                            if value.translation.height < -24 {
                                if !notesExpanded { notesExpanded = true }
                            } else if value.translation.height > 24 {
                                if notesExpanded { notesExpanded = false }
                            }
                        }
                )
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        // 给导航栏实色背景，避免下方视频的黑色背景透上来盖住标题
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarBackground(LPColor.card, for: .navigationBar)
    }

    // MARK: - 播放控制 + 时间轴

    private var controls: some View {
        VStack(spacing: 8) {
            TimelineBar(
                duration: playerController.duration,
                currentTime: playerController.currentTime,
                noteMarkers: notes.map(\.timestamp),
                onSeek: { playerController.seek(to: $0) }
            )

            HStack(spacing: 28) {
                Button { playerController.skip(-10) } label: {
                    Image(systemName: "gobackward.10")
                }
                Button { playerController.togglePlay() } label: {
                    Image(systemName: playerController.isPlaying ? "pause.fill" : "play.fill")
                        .font(.title2)
                }
                Button { playerController.skip(10) } label: {
                    Image(systemName: "goforward.10")
                }
            }
            .font(.title3)
            .foregroundStyle(LPColor.text)
        }
    }

    // MARK: - Tab 条

    private var tabBar: some View {
        HStack(spacing: 0) {
            ForEach(WorkspaceTab.allCases, id: \.self) { tab in
                Button {
                    selectedTab = tab
                    // 点 tab → 自动缩小播放器、内容区升起（点缩小态的视频可恢复）
                    notesExpanded = true
                } label: {
                    VStack(spacing: 5) {
                        Text(tab.title)
                            .font(.subheadline.weight(selectedTab == tab ? .semibold : .regular))
                            .foregroundStyle(selectedTab == tab ? LPColor.accent : LPColor.textMute)
                        Capsule()
                            .fill(selectedTab == tab ? AnyShapeStyle(LPGradient.primary) : AnyShapeStyle(Color.clear))
                            .frame(height: 3)
                            .padding(.horizontal, 18)
                    }
                }
                .frame(maxWidth: .infinity)
            }
        }
        .padding(.top, 8)
        .background(LPColor.card)
    }

    // MARK: - Tab 内容

    @ViewBuilder
    private var tabContent: some View {
        switch selectedTab {
        case .notes:
            NotesTab(
                sessionId: sessionId,
                currentTime: playerController.currentTime,
                onSeek: { playerController.seek(to: $0) }
            )
        case .transcript:
            TranscriptTab(
                backendSessionId: sessionId.uuidString.lowercased(),
                currentTime: playerController.currentTime,
                onSeek: { playerController.seek(to: $0) }
            )
        case .insights:
            // 后端 session id 是小写 UUID；Swift uuidString 默认大写 → 必须小写化，否则 404
            InsightsTab(
                backendSessionId: sessionId.uuidString.lowercased(),
                onSeek: { playerController.seek(to: $0) }
            )
        case .clips:
            // 后端 session id 是小写 UUID（同 InsightsTab）
            HighlightsTab(
                backendSessionId: sessionId.uuidString.lowercased(),
                onSeek: { playerController.seek(to: $0) }
            )
        }
    }

}

enum WorkspaceTab: CaseIterable {
    case notes, transcript, insights, clips

    var title: String {
        switch self {
        case .notes: return "笔记"
        case .transcript: return "话术"
        case .insights: return "洞察"
        case .clips: return "高光"
        }
    }
}
