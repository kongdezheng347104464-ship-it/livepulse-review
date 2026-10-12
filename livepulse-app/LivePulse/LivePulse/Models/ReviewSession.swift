//
//  ReviewSession.swift
//  LivePulse
//
//  「复盘场次」本地实体 —— 用户画的"直播 A/B/C"那一层。
//  是 Note 的稳定父节点，也是未来"历史复盘列表"的数据基础。
//
//  SwiftData @Model（iOS 17+）。
//

import Foundation
import SwiftData

@Model
final class ReviewSession {
    /// 稳定本地 id（笔记通过 sessionId 挂这个）
    @Attribute(.unique) var id: UUID
    /// 场次标题，如 "美妆直播 06-08"
    var title: String
    var createdAt: Date
    var updatedAt: Date

    // 视频元数据快照（即使原视频文件被清理，这些信息仍在）
    var durationSec: Double
    var width: Int
    var height: Int

    /// 【已弃用·保留字段】曾用于把视频拷到永久目录做历史复盘。
    /// 现在不拷贝视频（避免在手机重复占用存储），此字段永远为 nil。
    /// 保留只为不动 SwiftData schema（删字段有迁移风险）。
    var videoFileName: String?

    /// 主播名（上传时选已有或新建）
    var anchorName: String?
    /// 关联的后端主播 id（选已有主播时有；新建主播时为 nil，等上传到后端再回填）
    var anchorId: String?

    // 预留未来字段（先不用，避免日后迁移）
    /// 上传到 backend 后回填的 session_id
    var backendSessionId: String?
    /// 是否已上传分析
    var isUploaded: Bool

    init(
        id: UUID = UUID(),
        title: String,
        durationSec: Double,
        width: Int,
        height: Int,
        videoFileName: String? = nil,
        anchorName: String? = nil,
        anchorId: String? = nil
    ) {
        self.id = id
        self.title = title
        self.createdAt = Date()
        self.updatedAt = Date()
        self.durationSec = durationSec
        self.width = width
        self.height = height
        self.videoFileName = videoFileName
        self.anchorName = anchorName
        self.anchorId = anchorId
        self.backendSessionId = nil
        self.isUploaded = false
    }

    var isPortrait: Bool { height > width }
}
