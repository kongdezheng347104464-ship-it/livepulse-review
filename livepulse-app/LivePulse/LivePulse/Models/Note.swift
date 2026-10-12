//
//  Note.swift
//  LivePulse
//
//  时间锚笔记 —— 产品未来最值钱的数据资产。
//  挂在 ReviewSession 下（通过 sessionId），按时间点排序。
//
//  SwiftData @Model（iOS 17+）。第一版只存 Markdown 文本，不做富文本编辑器。
//

import Foundation
import SwiftData

@Model
final class Note {
    @Attribute(.unique) var id: UUID

    /// 所属复盘场次（指向 ReviewSession.id）。平铺字段不用 @Relationship，
    /// 避免 iOS 17 早期级联删除的坑；查询用 sessionId 过滤。
    var sessionId: UUID

    /// 绑定的视频时间点（秒）
    var timestamp: Double

    /// 笔记内容（Markdown 文本）
    var content: String

    /// 标签（预留，第一版不查，先存着）
    var tags: [String]

    /// 收藏（⭐）—— 未来"高光话术库"的地基
    var isFavorite: Bool

    /// 后端笔记 id（同步成功后回填）。非 nil = 已备份到后端；
    /// nil = 仅本地（离线创建或同步失败，下次会补推）。
    var backendId: String?

    var createdAt: Date
    var updatedAt: Date

    init(
        id: UUID = UUID(),
        sessionId: UUID,
        timestamp: Double,
        content: String,
        tags: [String] = [],
        isFavorite: Bool = false
    ) {
        self.id = id
        self.sessionId = sessionId
        self.timestamp = timestamp
        self.content = content
        self.tags = tags
        self.isFavorite = isFavorite
        self.createdAt = Date()
        self.updatedAt = Date()
    }

    /// 时间戳友好显示，如 "31:20" / "1:05:30"
    var timestampText: String {
        let total = Int(timestamp.rounded())
        let h = total / 3600
        let m = (total % 3600) / 60
        let s = total % 60
        if h > 0 {
            return String(format: "%d:%02d:%02d", h, m, s)
        }
        return String(format: "%d:%02d", m, s)
    }
}
