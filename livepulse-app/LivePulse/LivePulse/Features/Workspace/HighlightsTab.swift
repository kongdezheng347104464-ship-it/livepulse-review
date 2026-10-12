//
//  HighlightsTab.swift
//  LivePulse
//
//  高光时刻 Tab（Sprint 6B）：列 AI 识别的可分享高光（score>=60），
//  点「跳转」预览、点「导出」后端切片 → 系统分享 sheet（存相册 / 转剪映）。
//  我们负责"切哪里"，剪映负责"切得好看"。详见 docs/05-sprint6-highlights.md。
//

import SwiftUI

struct HighlightsTab: View {
    /// 后端 session id（小写 UUID）
    let backendSessionId: String
    /// 点高光跳转视频
    let onSeek: (Double) -> Void

    @State private var highlights: [Highlight] = []
    @State private var isLoading = true
    @State private var errorMessage: String?

    /// 正在导出的高光 id（驱动该卡片的 spinner）
    @State private var exportingId: String?
    /// 正在批量导出全部高光
    @State private var exportingAll = false
    /// 导出完成、待分享的本地 clip（触发 share sheet）
    @State private var shareItem: ShareItem?
    @State private var exportError: String?

    var body: some View {
        Group {
            if isLoading {
                loadingState
            } else if let errorMessage {
                messageState(icon: "exclamationmark.triangle", text: errorMessage, showRefresh: true)
            } else if highlights.isEmpty {
                emptyState
            } else {
                content
            }
        }
        .task { await load() }
        .sheet(item: $shareItem) { item in
            ActivityView(items: item.urls)
        }
        .alert("导出失败", isPresented: .constant(exportError != nil)) {
            Button("好") { exportError = nil }
        } message: {
            Text(exportError ?? "")
        }
    }

    // MARK: - 内容

    private var content: some View {
        ScrollView {
            VStack(spacing: LPSpacing.md) {
                Text("AI 识别的高光片段，导出后可在剪映精修发布")
                    .font(.caption)
                    .foregroundStyle(LPColor.textMute)
                    .frame(maxWidth: .infinity, alignment: .leading)

                ForEach(Array(highlights.enumerated()), id: \.element.id) { index, h in
                    highlightCard(rank: index + 1, h)
                }

                Button {
                    Task { await exportAll() }
                } label: {
                    HStack {
                        if exportingAll { ProgressView().tint(.white) }
                        Image(systemName: "square.and.arrow.up.on.square")
                        Text(exportingAll ? "导出中…" : "导出全部高光")
                    }
                }
                .buttonStyle(LPPrimaryButtonStyle())
                .disabled(exportingId != nil || exportingAll)
                .padding(.top, LPSpacing.xs)
            }
            .padding(16)
        }
    }

    private func highlightCard(rank: Int, _ h: Highlight) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text("#\(rank)")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(LPColor.accent)
                Text(timeRangeText(h))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(LPColor.textMid)
                Spacer()
                Text("\(Int(h.score.rounded()))")
                    .font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(.white)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(LPColor.score(for: h.score))
                    .clipShape(Capsule())
            }

            Text(h.reason)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(LPColor.text)
                .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: 20) {
                Button {
                    onSeek(h.startSec)
                } label: {
                    Label("跳转", systemImage: "play.circle")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(LPColor.accent)
                }
                Spacer()
                Button {
                    Task { await export(h) }
                } label: {
                    if exportingId == h.id {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small).tint(LPColor.accent)
                            Text("导出中…").font(.caption.weight(.medium))
                        }
                        .foregroundStyle(LPColor.textMute)
                    } else {
                        Label("导出", systemImage: "square.and.arrow.up")
                            .font(.caption.weight(.medium))
                            .foregroundStyle(LPColor.accent)
                    }
                }
                .disabled(exportingId != nil)
            }
            .buttonStyle(.plain)
        }
        .padding(14)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
    }

    // MARK: - 状态

    private var loadingState: some View {
        VStack(spacing: LPSpacing.sm) {
            ProgressView().tint(LPColor.accent)
            Text("加载高光…").font(.subheadline).foregroundStyle(LPColor.textMute)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// 空态是正常情况（约 36% 的场），不是错误。降级为空、绝不伪造高光。
    private var emptyState: some View {
        VStack(spacing: LPSpacing.sm) {
            Image("EmptyNoHighlights")
                .resizable().scaledToFit()
                .frame(width: 130, height: 130)
            Text("本场暂无可分享高光")
                .font(.headline).foregroundStyle(LPColor.textMid)
            Text("这场的转化 / 互动信号较弱，AI 没找到值得单独分享的片段")
                .font(.caption).foregroundStyle(LPColor.textMute)
                .multilineTextAlignment(.center).padding(.horizontal, 40)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func messageState(icon: String, text: String, showRefresh: Bool) -> some View {
        VStack(spacing: LPSpacing.sm) {
            Image(systemName: icon).font(.system(size: 40)).foregroundStyle(LPColor.textFaint)
            Text(text)
                .font(.subheadline).foregroundStyle(LPColor.textMute)
                .multilineTextAlignment(.center).padding(.horizontal, 32)
            if showRefresh {
                Button("刷新") { Task { await load() } }
                    .font(.subheadline).foregroundStyle(LPColor.accent)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - 加载 / 导出

    private func load() async {
        isLoading = true
        errorMessage = nil
        do {
            let resp: HighlightsResponse = try await APIClient.shared.get(
                "/sessions/\(backendSessionId)/highlights"
            )
            highlights = resp.items
        } catch {
            errorMessage = (error as? APIError)?.errorDescription ?? "加载失败"
        }
        isLoading = false
    }

    private func export(_ h: Highlight) async {
        exportingId = h.id
        defer { exportingId = nil }
        do {
            let resp: ClipExportResponse = try await APIClient.shared.post(
                "/sessions/\(backendSessionId)/highlights/\(h.id)/export"
            )
            guard let url = URL(string: resp.clipUrl) else {
                exportError = "导出链接无效"
                return
            }
            // 下载 presigned clip 到本地临时文件，再交给系统分享 sheet。
            let (tmp, _) = try await URLSession.shared.download(from: url)
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent("LivePulse高光_\(h.id).mp4")
            try? FileManager.default.removeItem(at: dest)
            try FileManager.default.moveItem(at: tmp, to: dest)
            shareItem = ShareItem(urls: [dest])
        } catch {
            exportError = (error as? APIError)?.errorDescription ?? "切片导出失败，请重试"
        }
    }

    /// 批量导出全部高光 → 一次分享多个片段
    private func exportAll() async {
        exportingAll = true
        defer { exportingAll = false }
        var urls: [URL] = []
        for h in highlights {
            do {
                let resp: ClipExportResponse = try await APIClient.shared.post(
                    "/sessions/\(backendSessionId)/highlights/\(h.id)/export"
                )
                guard let url = URL(string: resp.clipUrl) else { continue }
                let (tmp, _) = try await URLSession.shared.download(from: url)
                let dest = FileManager.default.temporaryDirectory
                    .appendingPathComponent("LivePulse高光_\(h.id).mp4")
                try? FileManager.default.removeItem(at: dest)
                try FileManager.default.moveItem(at: tmp, to: dest)
                urls.append(dest)
            } catch { continue }
        }
        if urls.isEmpty {
            exportError = "导出失败，请重试"
        } else {
            shareItem = ShareItem(urls: urls)
        }
    }

    private func timeRangeText(_ h: Highlight) -> String {
        "\(fmtTime(h.startSec))–\(fmtTime(h.endSec))"
    }

    private func fmtTime(_ sec: Double) -> String {
        let s = Int(sec.rounded())
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

/// 包一层 Identifiable，让下载好的 clip URL 能驱动 `.sheet(item:)`。
private struct ShareItem: Identifiable {
    let id = UUID()
    let urls: [URL]
}

/// UIActivityViewController 的 SwiftUI 封装：系统分享 sheet
/// （存视频到相册 / 发给剪映 / 其他 app）。
private struct ActivityView: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}
