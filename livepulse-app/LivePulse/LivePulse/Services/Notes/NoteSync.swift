//
//  NoteSync.swift
//  LivePulse
//
//  笔记后端同步：本地 SwiftData 是富笔记（含收藏/标签）的主，
//  把"内容 + 时间点"推到后端做备份（丢手机/重装能恢复 + 跨设备/web 可见）。
//  收藏/标签是 iOS 增强，留本地。
//
//  策略：尽力同步，失败不阻塞本地操作（笔记永远先本地可用）。
//

import Foundation

// MARK: - DTO

private struct CreateNoteBody: Encodable {
    let timestampSec: Int
    let noteText: String
    enum CodingKeys: String, CodingKey {
        case timestampSec = "timestamp_sec"
        case noteText = "note_text"
    }
}

private struct UpdateNoteBody: Encodable {
    let noteText: String
    enum CodingKeys: String, CodingKey {
        case noteText = "note_text"
    }
}

private struct CreatedNoteResponse: Decodable {
    let id: String
}

struct BackendNote: Decodable {
    let id: String
    let timestampSec: Double
    let noteText: String
    enum CodingKeys: String, CodingKey {
        case id
        case timestampSec = "timestamp_sec"
        case noteText = "note_text"
    }
}

private struct BackendNotesList: Decodable {
    let items: [BackendNote]
}

// MARK: - Sync

enum NoteSync {
    /// 创建 → 返回后端 note id（失败返回 nil，本地照常保留）
    static func create(sessionId: String, timestampSec: Int, text: String) async -> String? {
        do {
            let res: CreatedNoteResponse = try await APIClient.shared.post(
                "/sessions/\(sessionId)/notes",
                body: CreateNoteBody(timestampSec: timestampSec, noteText: text)
            )
            return res.id
        } catch {
            return nil
        }
    }

    /// 编辑（已同步的笔记）。失败静默（本地已改）。
    static func update(sessionId: String, backendId: String, text: String) async {
        try? await APIClient.shared.patchVoid(
            "/sessions/\(sessionId)/notes/\(backendId)",
            body: UpdateNoteBody(noteText: text)
        )
    }

    /// 删除后端笔记。失败静默。
    static func delete(sessionId: String, backendId: String) async {
        try? await APIClient.shared.delete("/sessions/\(sessionId)/notes/\(backendId)")
    }

    /// 拉后端笔记（用于跨设备/重装恢复的合并）。失败返回 nil（不动本地）。
    static func fetch(sessionId: String) async -> [BackendNote]? {
        do {
            let res: BackendNotesList = try await APIClient.shared.get("/sessions/\(sessionId)/notes")
            return res.items
        } catch {
            return nil
        }
    }
}
