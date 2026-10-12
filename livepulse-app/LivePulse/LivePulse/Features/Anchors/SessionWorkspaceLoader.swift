//
//  SessionWorkspaceLoader.swift
//  LivePulse
//
//  点后端场次 → 拉 timeline 拿到 presigned 视频 URL → 进复盘工作台。
//  笔记本地存（按后端 session id 的 UUID 挂），可流式播放后端视频边看边记。
//

import SwiftUI

struct SessionWorkspaceLoader: View {
    let session: AnalyzedSession

    @State private var videoURL: URL?
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        Group {
            if isLoading {
                VStack(spacing: LPSpacing.md) {
                    ProgressView().tint(LPColor.accent)
                    Text("加载视频中…")
                        .font(.subheadline)
                        .foregroundStyle(LPColor.textMute)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(LPColor.bg)
            } else if let videoURL, let sid = UUID(uuidString: session.id) {
                VideoWorkspaceView(
                    sessionId: sid,
                    title: session.displayTitle,
                    videoURL: videoURL
                )
            } else {
                errorState
            }
        }
        .task { await load() }
    }

    private var errorState: some View {
        VStack(spacing: LPSpacing.md) {
            Image(systemName: "video.slash")
                .font(.system(size: 44))
                .foregroundStyle(LPColor.textFaint)
            Text(errorMessage ?? "这场暂时无法播放")
                .font(.subheadline)
                .foregroundStyle(LPColor.textMute)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Button("重试") { Task { await load() } }
                .foregroundStyle(LPColor.accent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(LPColor.bg)
        .navigationTitle(session.displayTitle)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        do {
            let res: TimelineResponse = try await APIClient.shared.get(
                "/sessions/\(session.id)/timeline"
            )
            if let urlStr = res.videoUrl, let url = URL(string: urlStr) {
                videoURL = url
            } else {
                errorMessage = "这场没有可播放的视频"
            }
        } catch {
            errorMessage = (error as? APIError)?.errorDescription ?? "加载失败"
        }
        isLoading = false
    }
}

/// /sessions/{id}/timeline 只取 video_url（其他字段后续按需加）
private struct TimelineResponse: Decodable {
    let videoUrl: String?
    enum CodingKeys: String, CodingKey {
        case videoUrl = "video_url"
    }
}
