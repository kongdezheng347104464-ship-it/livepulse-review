//
//  TranscriptTab.swift
//  LivePulse
//
//  话术 Tab：拉 backend 转写（主播话术），字幕跟读体验 —
//  高亮当前播放段 + 自动滚动跟随 + 点段跳转视频。
//
//  注：已分析场次后端已有转写（pipeline 跑过 ASR），直接展示；
//  端侧 ASR（上传时边传边显示）是 Sprint 4C 的渐进式优化，不影响这里。
//

import SwiftUI

// MARK: - 模型

private struct TranscriptResponse: Decodable {
    let segments: [TranscriptSegment]
}

struct TranscriptSegment: Decodable, Identifiable {
    let id: String
    let startSec: Double
    let endSec: Double
    let text: String
    let scriptType: String?

    enum CodingKeys: String, CodingKey {
        case id, text
        case startSec = "start_sec"
        case endSec = "end_sec"
        case scriptType = "script_type"
    }

    var timeText: String {
        let t = Int(startSec.rounded())
        let m = t / 60, s = t % 60
        return String(format: "%d:%02d", m, s)
    }

    var typeLabel: String? {
        switch scriptType {
        case "product_intro": return "产品"
        case "teaching": return "教学"
        case "opening", "hook": return "开场"
        case "closing": return "逼单"
        case "interaction": return "互动"
        case "chitchat": return "闲聊"
        default: return nil
        }
    }
}

// MARK: - 视图

struct TranscriptTab: View {
    let backendSessionId: String
    let currentTime: Double
    let onSeek: (Double) -> Void

    @State private var segments: [TranscriptSegment] = []
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var notReady = false

    // AI 选段点评
    @State private var selectionMode = false
    @State private var selectedIds: Set<String> = []
    @State private var rangeSheet = false
    @State private var rangeLoading = false
    @State private var rangeResult: RangeAnalysisResult?
    @State private var rangeError: String?

    // 历史点评
    @State private var history: [RangeAnalysisRecord] = []
    @State private var historySheet = false

    /// 当前播放时间命中的段 id
    private var currentSegmentId: String? {
        segments.first { currentTime >= $0.startSec && currentTime < $0.endSec }?.id
            ?? segments.last { $0.startSec <= currentTime }?.id
    }

    var body: some View {
        Group {
            if isLoading {
                stateView(spinner: true, icon: nil, text: "加载话术…")
            } else if notReady {
                stateView(spinner: false, icon: "hourglass", text: "分析还在进行中，完成后显示话术")
            } else if let errorMessage {
                stateView(spinner: false, icon: "exclamationmark.triangle", text: errorMessage)
            } else if segments.isEmpty {
                stateView(spinner: false, icon: "text.bubble", text: "本场没有识别到话术")
            } else {
                VStack(spacing: 0) {
                    selectionBar
                    list
                }
            }
        }
        .task { await load() }
        // 新点评结果
        .sheet(isPresented: $rangeSheet) {
            NavigationStack {
                RangeAnalysisResultView(
                    loading: rangeLoading,
                    result: rangeResult,
                    errorMessage: rangeError
                )
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("完成") { rangeSheet = false }
                    }
                }
            }
        }
        // 历史点评列表
        .sheet(isPresented: $historySheet) {
            RangeAnalysisHistorySheet(records: history) { historySheet = false }
        }
    }

    // MARK: - 选段工具条

    @ViewBuilder
    private var selectionBar: some View {
        if selectionMode {
            HStack {
                Button("取消") {
                    selectionMode = false
                    selectedIds.removeAll()
                }
                .foregroundStyle(LPColor.textMid)
                Spacer()
                Text("已选 \(selectedIds.count) 段")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(LPColor.text)
                Spacer()
                Button("AI 点评") { runRangeAnalysis() }
                    .fontWeight(.semibold)
                    .foregroundStyle(selectedIds.isEmpty ? LPColor.textFaint : LPColor.accent)
                    .disabled(selectedIds.isEmpty)
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .background(LPColor.accentSoft)
        } else {
            HStack(spacing: 0) {
                Button {
                    selectionMode = true
                } label: {
                    HStack {
                        Image(systemName: "sparkles")
                        Text("选段让 AI 点评").fontWeight(.medium)
                        Spacer()
                    }
                    .foregroundStyle(LPColor.accent)
                }
                if !history.isEmpty {
                    Button {
                        historySheet = true
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "clock.arrow.circlepath")
                            Text("记录 \(history.count)")
                        }
                        .font(.subheadline)
                        .foregroundStyle(LPColor.accentText)
                    }
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .background(LPColor.accentSoft)
        }
    }

    private var list: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 8) {
                    ForEach(segments) { seg in
                        row(seg, isCurrent: seg.id == currentSegmentId)
                            .id(seg.id)
                    }
                }
                .padding(16)
            }
            // 当前段变化 → 自动滚动跟随（选段模式下不跟随，避免打断选择）
            .onChange(of: currentSegmentId) { _, newId in
                guard !selectionMode, let newId else { return }
                withAnimation(.easeInOut(duration: 0.25)) {
                    proxy.scrollTo(newId, anchor: .center)
                }
            }
        }
    }

    private func row(_ seg: TranscriptSegment, isCurrent: Bool) -> some View {
        let isSelected = selectedIds.contains(seg.id)
        return Button {
            if selectionMode {
                if isSelected { selectedIds.remove(seg.id) } else { selectedIds.insert(seg.id) }
            } else {
                onSeek(seg.startSec)
            }
        } label: {
            HStack(alignment: .top, spacing: 10) {
                if selectionMode {
                    Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(isSelected ? LPColor.accent : LPColor.textFaint)
                        .padding(.top, 1)
                }
                VStack(spacing: 3) {
                    Text(seg.timeText)
                        .font(.caption2.weight(.semibold).monospacedDigit())
                        .foregroundStyle(isCurrent ? LPColor.accent : LPColor.textMute)
                    if let t = seg.typeLabel {
                        Text(t)
                            .font(.system(size: 9))
                            .foregroundStyle(LPColor.textMute)
                    }
                }
                .frame(width: 38)

                Text(seg.text)
                    .font(.subheadline)
                    .foregroundStyle(isCurrent ? LPColor.text : LPColor.textMid)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(12)
            .background(rowBackground(isCurrent: isCurrent, isSelected: isSelected))
            .clipShape(RoundedRectangle(cornerRadius: LPRadius.md, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: LPRadius.md, style: .continuous)
                    .stroke(rowBorder(isCurrent: isCurrent, isSelected: isSelected), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    private func rowBackground(isCurrent: Bool, isSelected: Bool) -> Color {
        if isSelected { return LPColor.accentSoft }
        if isCurrent { return LPColor.accentSoft }
        return LPColor.card
    }

    private func rowBorder(isCurrent: Bool, isSelected: Bool) -> Color {
        if isSelected { return LPColor.accent }
        if isCurrent { return LPColor.accent.opacity(0.4) }
        return LPColor.border
    }

    // MARK: - 触发 AI 点评

    private func runRangeAnalysis() {
        let ids = Array(selectedIds)
        guard !ids.isEmpty else { return }
        rangeResult = nil
        rangeError = nil
        rangeLoading = true
        rangeSheet = true
        Task {
            do {
                let res: RangeAnalysisResponse = try await APIClient.shared.post(
                    "/sessions/\(backendSessionId)/range-analysis",
                    body: RangeAnalysisRequest(segmentIds: ids)
                )
                rangeResult = res.result
            } catch {
                rangeError = (error as? APIError)?.errorDescription ?? "点评失败，请重试"
            }
            rangeLoading = false
            // 点评完退出选段模式 + 刷新历史
            selectionMode = false
            selectedIds.removeAll()
            await loadHistory()
        }
    }

    private func loadHistory() async {
        if let res: RangeAnalysisListResponse = try? await APIClient.shared.get(
            "/sessions/\(backendSessionId)/range-analyses"
        ) {
            history = res.items
        }
    }

    private func stateView(spinner: Bool, icon: String?, text: String) -> some View {
        VStack(spacing: LPSpacing.sm) {
            if spinner {
                ProgressView().tint(LPColor.accent)
            } else if let icon {
                Image(systemName: icon).font(.system(size: 40)).foregroundStyle(LPColor.textFaint)
            }
            Text(text)
                .font(.subheadline).foregroundStyle(LPColor.textMute)
                .multilineTextAlignment(.center).padding(.horizontal, 32)
            if !spinner {
                Button("刷新") { Task { await load() } }
                    .font(.subheadline).foregroundStyle(LPColor.accent)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func load() async {
        isLoading = true
        notReady = false
        errorMessage = nil
        do {
            let res: TranscriptResponse = try await APIClient.shared.get(
                "/sessions/\(backendSessionId)/transcript"
            )
            segments = res.segments
            await loadHistory()   // 顺带拉历史点评
        } catch let err as APIError {
            // 404 = 该场次/转写还没就绪
            if case .server(let status, _) = err, status == 404 {
                notReady = true
            } else if case .invalidResponse = err {
                notReady = true
            } else {
                errorMessage = err.errorDescription
            }
        } catch {
            errorMessage = "加载失败"
        }
        isLoading = false
    }
}
