//
//  NoteEditorView.swift
//  LivePulse
//
//  笔记编辑器（新建 / 编辑）。第一版只支持 Markdown 纯文本输入，
//  底部给一行 Markdown 语法提示，不做 WYSIWYG 富文本编辑器。
//

import SwiftUI
import SwiftData

struct NoteEditorView: View {
    let sessionId: UUID
    let timestamp: Double
    /// 非 nil = 编辑已有笔记；nil = 新建
    let existingNote: Note?

    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss

    @State private var text: String = ""
    @FocusState private var focused: Bool

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 0) {
                // 时间锚提示
                HStack(spacing: 4) {
                    Image(systemName: "clock")
                    Text(timestampText)
                        .monospacedDigit()
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(LPColor.accent)
                .padding(.horizontal, 16)
                .padding(.top, 12)

                // 文本输入
                TextEditor(text: $text)
                    .focused($focused)
                    .font(.body)
                    .padding(.horizontal, 12)
                    .padding(.top, 8)

                // Markdown 语法提示
                Text("支持 Markdown：**重点** · # 标题 · - 列表")
                    .font(.caption2)
                    .foregroundStyle(LPColor.textMute)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)
            }
            .navigationTitle(existingNote == nil ? "记笔记" : "编辑笔记")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") { save() }
                        .fontWeight(.semibold)
                        .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .onAppear {
                text = existingNote?.content ?? ""
                // 不在 onAppear 自动聚焦：否则 sheet 先弹到 medium、键盘再把 sheet
                // 二次抬高，出现"先停一个高度再继续升高"的两段跳。
                // 改为用户点输入框时键盘单独平滑弹出。
            }
        }
    }

    private var timestampText: String {
        let total = Int(timestamp.rounded())
        let h = total / 3600, m = (total % 3600) / 60, s = total % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, s) }
        return String(format: "%d:%02d", m, s)
    }

    private func save() {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        let backendSid = sessionId.uuidString.lowercased()
        let tsSec = Int(timestamp)

        if let note = existingNote {
            note.content = trimmed
            note.updatedAt = Date()
            // 推后端备份（已同步 → PATCH；之前没同步成功 → 当新建补推）
            Task { @MainActor in
                if let bid = note.backendId {
                    await NoteSync.update(sessionId: backendSid, backendId: bid, text: trimmed)
                } else if let newId = await NoteSync.create(sessionId: backendSid, timestampSec: tsSec, text: trimmed) {
                    note.backendId = newId
                }
            }
        } else {
            let note = Note(sessionId: sessionId, timestamp: timestamp, content: trimmed)
            context.insert(note)
            Task { @MainActor in
                if let newId = await NoteSync.create(sessionId: backendSid, timestampSec: tsSec, text: trimmed) {
                    note.backendId = newId
                }
            }
        }
        dismiss()
    }
}
