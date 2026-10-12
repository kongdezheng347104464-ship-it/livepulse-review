//
//  NotesTab.swift
//  LivePulse
//
//  笔记 Tab：在当前播放时间点记笔记 + 列表 + 点击跳转 + 收藏 + 删除（二次确认）。
//

import SwiftUI
import SwiftData

struct NotesTab: View {
    let sessionId: UUID
    let currentTime: Double
    let onSeek: (Double) -> Void

    @Environment(\.modelContext) private var context

    @Query private var notes: [Note]

    @State private var showEditor = false
    @State private var editingNote: Note?
    @State private var noteToDelete: Note?
    @State private var showDeleteConfirm = false

    init(
        sessionId: UUID,
        currentTime: Double,
        onSeek: @escaping (Double) -> Void
    ) {
        self.sessionId = sessionId
        self.currentTime = currentTime
        self.onSeek = onSeek
        let sid = sessionId
        _notes = Query(
            filter: #Predicate<Note> { $0.sessionId == sid },
            sort: [SortDescriptor(\Note.timestamp, order: .forward)]
        )
    }

    var body: some View {
        VStack(spacing: 0) {
            addBar

            if notes.isEmpty {
                emptyState
            } else {
                List {
                    ForEach(notes) { note in
                        NoteRow(
                            note: note,
                            onTapTime: { onSeek(note.timestamp) },
                            onToggleFav: { toggleFavorite(note) },
                            onEdit: { editingNote = note; showEditor = true }
                        )
                        .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
                        .listRowSeparator(.hidden)
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) {
                                noteToDelete = note
                                showDeleteConfirm = true
                            } label: {
                                Label("删除", systemImage: "trash")
                            }
                        }
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .background(LPColor.bg)
            }
        }
        .sheet(isPresented: $showEditor) {
            NoteEditorView(
                sessionId: sessionId,
                timestamp: editingNote?.timestamp ?? currentTime,
                existingNote: editingNote
            )
            .presentationDetents([.medium])
            .presentationDragIndicator(.visible)
        }
        .alert("删除笔记", isPresented: $showDeleteConfirm, presenting: noteToDelete) { note in
            Button("删除", role: .destructive) { deleteNote(note) }
            Button("取消", role: .cancel) {}
        } message: { note in
            Text("确定删除「\(note.timestampText)」这条笔记吗？删除后不可恢复。")
        }
        // 拉后端笔记合并（重装/跨设备恢复），每个 tab 实例只跑一次
        .task { await mergeBackendNotes() }
    }

    private var backendSessionId: String { sessionId.uuidString.lowercased() }

    private func deleteNote(_ note: Note) {
        if let bid = note.backendId {
            let sid = backendSessionId
            Task { await NoteSync.delete(sessionId: sid, backendId: bid) }
        }
        context.delete(note)
    }

    @State private var didMerge = false

    private func mergeBackendNotes() async {
        guard !didMerge else { return }
        didMerge = true
        guard let backend = await NoteSync.fetch(sessionId: backendSessionId) else { return }
        // 本地已有的后端 id（避免重复插入）
        let localIds = Set(notes.compactMap { $0.backendId })
        for bn in backend where !localIds.contains(bn.id) {
            let n = Note(
                sessionId: sessionId,
                timestamp: bn.timestampSec,
                content: bn.noteText
            )
            n.backendId = bn.id   // 标记已同步，下次不重复拉
            context.insert(n)
        }
    }

    private var addBar: some View {
        Button {
            editingNote = nil
            showEditor = true
        } label: {
            HStack {
                Image(systemName: "plus.circle.fill")
                Text("在 \(timeText(currentTime)) 记笔记")
                    .fontWeight(.medium)
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .foregroundStyle(LPColor.accent)
        }
        .background(LPColor.accentSoft)
    }

    private var emptyState: some View {
        VStack(spacing: LPSpacing.sm) {
            Spacer()
            Image("EmptyNoNotes")
                .resizable().scaledToFit()
                .frame(width: 130, height: 130)
            Text("还没有笔记")
                .font(.headline).foregroundStyle(LPColor.textMid)
            Text("边看边记 · 笔记会自动绑定当前时间点")
                .font(.subheadline)
                .foregroundStyle(LPColor.textMute)
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .background(LPColor.bg)
    }

    // MARK: - 动作

    private func toggleFavorite(_ note: Note) {
        note.isFavorite.toggle()
        note.updatedAt = Date()
    }

    private func timeText(_ sec: Double) -> String {
        let total = Int(sec.rounded())
        let h = total / 3600, m = (total % 3600) / 60, s = total % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, s) }
        return String(format: "%d:%02d", m, s)
    }
}

// MARK: - 单条笔记行

private struct NoteRow: View {
    let note: Note
    let onTapTime: () -> Void
    let onToggleFav: () -> Void
    let onEdit: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Button(action: onTapTime) {
                    HStack(spacing: 4) {
                        Image(systemName: "clock")
                            .font(.caption2)
                        Text(note.timestampText)
                            .font(.caption.weight(.semibold).monospacedDigit())
                    }
                    .foregroundStyle(LPColor.accent)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(LPColor.accentSoft)
                    .clipShape(Capsule())
                }
                .buttonStyle(.plain)

                Spacer()

                Button(action: onToggleFav) {
                    Image(systemName: note.isFavorite ? "star.fill" : "star")
                        .foregroundStyle(note.isFavorite ? LPColor.scoreMid : LPColor.textFaint)
                }
                .buttonStyle(.plain)
            }

            Text(markdownAttributed(note.content))
                .font(.subheadline)
                .foregroundStyle(LPColor.text)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(14)
        .background(LPColor.card)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
        .contentShape(Rectangle())
        .onTapGesture(perform: onEdit)
    }

    private func markdownAttributed(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        return (try? AttributedString(markdown: text, options: options))
            ?? AttributedString(text)
    }
}
