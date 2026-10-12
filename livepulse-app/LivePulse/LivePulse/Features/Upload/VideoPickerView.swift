//
//  VideoPickerView.swift
//  LivePulse
//
//  Sprint 2：选视频 + 展示元数据 + 播放预览。
//  （转码 / 上传是 Sprint 3 的事，这里只验证"选 + 读取 + 预览"）
//

import SwiftUI
import AVKit
import SwiftData

@MainActor
struct VideoPickerView: View {
    @Environment(\.dismiss) private var dismiss

    @State private var showPicker = false
    @State private var asset: VideoAsset?
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var player: AVPlayer?

    // 上传
    @State private var uploader = ChunkedUploader()
    /// 上传完成后跳进的后端场次（流式播刚传的视频 + 记笔记）
    @State private var uploadedSession: AnalyzedSession?

    // 表单：主播（输入即搜索，可选已有可输新建）+ 标题
    @State private var anchors: [Anchor] = []
    @State private var anchorInput = ""
    /// 已选中的后端主播 id（从建议里点选时有；纯手输新名字时为 nil）
    @State private var pickedAnchorId: String?
    @State private var liveTitle = ""

    /// 输入框里匹配到的已有主播（输入非空 + 不完全等于已选名）
    private var anchorSuggestions: [Anchor] {
        let q = anchorInput.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return [] }
        // 已经精确选中了某主播就不再提示
        if anchors.contains(where: { $0.name == q && $0.id == pickedAnchorId }) { return [] }
        return anchors.filter { $0.name.localizedCaseInsensitiveContains(q) }.prefix(5).map { $0 }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: LPSpacing.lg) {
                    if let asset {
                        videoPreview(asset)
                        anchorForm
                        titleForm
                        uploadSection(asset)
                        if uploader.phase == .idle || uploader.phase == .failed {
                            reselectButton
                        }
                    } else {
                        emptyState
                    }

                    if let errorMessage {
                        Text(errorMessage)
                            .font(.footnote)
                            .foregroundStyle(LPColor.scoreLow)
                    }
                }
                .padding(LPSpacing.lg)
            }
            .background(LPColor.bg)
            .navigationTitle("选择录屏")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("关闭") { dismiss() }
                }
            }
            .sheet(isPresented: $showPicker) {
                VideoPicker(
                    onPicked: handlePicked,
                    onError: { err in errorMessage = err.localizedDescription }
                )
                .ignoresSafeArea()
            }
            .navigationDestination(item: $uploadedSession) { session in
                SessionWorkspaceLoader(session: session)
            }
        }
        .task { await loadAnchors() }
    }

    // MARK: - 主播表单（输入即搜索：输入名字，匹配到已有主播可点选，也可输新名）

    private var anchorForm: some View {
        VStack(alignment: .leading, spacing: LPSpacing.sm) {
            Text("主播")
                .font(.footnote.weight(.medium))
                .foregroundStyle(LPColor.textMid)

            TextField("输入主播名字，如：小满", text: $anchorInput)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .onChange(of: anchorInput) { _, _ in
                    // 手动改了文字 → 取消之前的精确选中（除非又匹配上）
                    pickedAnchorId = anchors.first { $0.name == anchorInput }?.id
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(LPColor.card)
                .overlay(
                    RoundedRectangle(cornerRadius: LPRadius.sm)
                        .stroke(LPColor.border, lineWidth: 1)
                )

            // 匹配到的已有主播建议
            if !anchorSuggestions.isEmpty {
                VStack(spacing: 0) {
                    ForEach(anchorSuggestions) { a in
                        Button {
                            anchorInput = a.name
                            pickedAnchorId = a.id
                        } label: {
                            HStack {
                                Text(a.name)
                                    .foregroundStyle(LPColor.text)
                                Text("\(a.sessionCount) 场")
                                    .font(.caption)
                                    .foregroundStyle(LPColor.textMute)
                                Spacer()
                                Text("已有")
                                    .font(.caption2)
                                    .foregroundStyle(LPColor.accent)
                            }
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        if a.id != anchorSuggestions.last?.id {
                            Divider().padding(.leading, 14)
                        }
                    }
                }
                .background(LPColor.card)
                .clipShape(RoundedRectangle(cornerRadius: LPRadius.sm))
                .overlay(
                    RoundedRectangle(cornerRadius: LPRadius.sm)
                        .stroke(LPColor.border, lineWidth: 1)
                )
            }
        }
    }

    // MARK: - 标题表单

    private var titleForm: some View {
        VStack(alignment: .leading, spacing: LPSpacing.sm) {
            Text("直播标题")
                .font(.footnote.weight(.medium))
                .foregroundStyle(LPColor.textMid)
            TextField("例如：周五晚美妆专场", text: $liveTitle)
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(LPColor.card)
                .overlay(
                    RoundedRectangle(cornerRadius: LPRadius.sm)
                        .stroke(LPColor.border, lineWidth: 1)
                )
        }
    }

    // MARK: - 上传

    @ViewBuilder
    private func uploadSection(_ asset: VideoAsset) -> some View {
        switch uploader.phase {
        case .idle, .failed:
            let isRetry = uploader.phase == .failed
            Button {
                Task { await runUpload(asset) }
            } label: {
                HStack {
                    Image(systemName: isRetry ? "arrow.clockwise" : "arrow.up.circle.fill")
                    Text(isRetry ? "继续上传" : "上传并分析")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 15)
                .background(canEnter ? AnyShapeStyle(LPGradient.primary) : AnyShapeStyle(LPColor.textFaint))
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: LPRadius.md, style: .continuous))
            }
            .disabled(!canEnter)

        case .uploading, .finalizing, .done:
            VStack(alignment: .leading, spacing: LPSpacing.sm) {
                HStack {
                    Text(uploadStatusText)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(LPColor.text)
                    Spacer()
                    Text("\(Int(uploader.progress * 100))%")
                        .font(.subheadline.weight(.bold).monospacedDigit())
                        .foregroundStyle(LPColor.accent)
                }
                ProgressView(value: uploader.progress)
                    .tint(LPColor.accent)
            }
            .lpCard()
        }
    }

    private var uploadStatusText: String {
        switch uploader.phase {
        case .uploading: return "上传中… \(Int(uploader.progress * 100))%"
        case .finalizing: return "上传完成，正在启动分析…"
        case .done: return "已启动分析，进入复盘…"
        default: return ""
        }
    }

    /// 是否能上传：主播名非空
    private var canEnter: Bool {
        !anchorInput.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private func runUpload(_ asset: VideoAsset) async {
        let anchorName = anchorInput.trimmingCharacters(in: .whitespaces)
        // 输入名精确等于某已有主播 → 带 anchor_id（绑已有）；否则 new_anchor_name（建新）
        let anchorId = pickedAnchorId ?? anchors.first { $0.name == anchorName }?.id
        let title = liveTitle.trimmingCharacters(in: .whitespaces).isEmpty
            ? defaultTitle() : liveTitle.trimmingCharacters(in: .whitespaces)

        await uploader.upload(
            fileURL: asset.url,
            filename: asset.url.lastPathComponent,
            anchorId: anchorId,
            newAnchorName: anchorId == nil ? anchorName : nil,
            title: title
        )

        if uploader.phase == .done, let r = uploader.result {
            // 跳进刚上传的后端场次工作台（流式播 master + 记笔记，后端同时在分析）
            uploadedSession = AnalyzedSession(
                id: r.sessionId,
                anchorId: anchorId,
                anchorName: r.anchorName,
                title: r.title,
                durationSec: Int(asset.durationSec),
                compositeScore: nil,
                status: r.status,
                scores: nil,
                startTime: nil,
                createdAt: nil
            )
        } else if let msg = uploader.errorMessage {
            errorMessage = msg
        }
    }

    private func defaultTitle() -> String {
        let f = DateFormatter()
        f.dateFormat = "MM-dd HH:mm"
        return "直播复盘 \(f.string(from: Date()))"
    }

    /// 拉主播列表（进表单时，给输入框做匹配建议用）
    private func loadAnchors() async {
        do {
            let res: AnchorListResponse = try await APIClient.shared.get(
                "/anchors/", query: ["page_size": "100"]
            )
            anchors = res.items
        } catch {
            // 拉不到不阻塞，用户仍可手输新主播名
        }
    }

    // MARK: - 子视图

    private var emptyState: some View {
        VStack(spacing: LPSpacing.lg) {
            Text("上传直播录屏")
                .font(.system(size: 22, weight: .bold))
                .foregroundStyle(LPColor.text)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, LPSpacing.md)

            // 虚线浅紫上传框（点击选视频）
            Button {
                errorMessage = nil
                showPicker = true
            } label: {
                VStack(spacing: LPSpacing.md) {
                    Image(systemName: "icloud.and.arrow.up.fill")
                        .font(.system(size: 34, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 72, height: 72)
                        .background(LPGradient.primary)
                        .clipShape(Circle())
                        .lpSoftShadow()
                    Text("点击选择直播录屏")
                        .font(.headline)
                        .foregroundStyle(LPColor.text)
                    Text("支持 mp4 · 竖屏 / 横屏 · 单个最大 2GB")
                        .font(.caption)
                        .foregroundStyle(LPColor.textMute)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 48)
                .background(LPColor.accentSoft)
                .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous)
                        .stroke(LPColor.accent.opacity(0.4),
                                style: StrokeStyle(lineWidth: 1.5, dash: [7, 5]))
                )
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: LPSpacing.sm) {
                uploadTip("上传中请不要退出页面")
                uploadTip("支持断点续传")
                uploadTip("上传完成后自动启动分析")
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func uploadTip(_ text: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.circle.fill")
                .font(.caption).foregroundStyle(LPColor.accent)
            Text(text).font(.footnote).foregroundStyle(LPColor.textMid)
        }
    }

    private func videoPreview(_ asset: VideoAsset) -> some View {
        VStack {
            if let player {
                VideoPlayer(player: player)
                    .aspectRatio(
                        CGFloat(asset.width) / CGFloat(max(asset.height, 1)),
                        contentMode: .fit
                    )
                    .frame(maxHeight: 420)
                    .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
                    .lpCardShadow()
            }
        }
    }

    private var reselectButton: some View {
        Button {
            errorMessage = nil
            showPicker = true
        } label: {
            Text("重新选择")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(LPColor.accent)
        }
    }

    // MARK: - 动作

    private func handlePicked(_ url: URL) {
        errorMessage = nil
        isLoading = true
        Task {
            defer { isLoading = false }
            do {
                let loaded = try await VideoAssetLoader.load(from: url)
                asset = loaded
                player = AVPlayer(url: url)
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

#Preview {
    VideoPickerView()
}
