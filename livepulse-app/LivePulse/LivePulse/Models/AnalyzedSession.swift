//
//  AnalyzedSession.swift
//  LivePulse
//
//  已分析场次（后端实体）。对应 backend GET /sessions/。
//  注意：与本地 ReviewSession（SwiftData）是两套数据 —— 这是云端已分析的。
//

import Foundation

struct AnalyzedSession: Codable, Identifiable, Hashable {
    let id: String
    let anchorId: String?
    let anchorName: String?
    let title: String?
    let durationSec: Int?
    let compositeScore: Double?
    let status: String
    let scores: Scores?
    let startTime: String?
    let createdAt: String?

    struct Scores: Codable, Hashable {
        let engagement: Double?
        let conversion: Double?
        let content: Double?
    }

    enum CodingKeys: String, CodingKey {
        case id, title, status, scores
        case anchorId = "anchor_id"
        case anchorName = "anchor_name"
        case durationSec = "duration_sec"
        case compositeScore = "composite_score"
        case startTime = "start_time"
        case createdAt = "created_at"
    }

    var isAnalyzed: Bool { status == "analyzed" }

    var displayTitle: String { (title?.isEmpty == false ? title : nil) ?? "未命名场次" }

    /// 综合分整数显示（未分析或无分显示 "—"）
    var scoreText: String {
        guard isAnalyzed, let s = compositeScore else { return "—" }
        return String(Int(s.rounded()))
    }

    /// 时长友好显示，如 "15 分钟"
    var durationText: String? {
        guard let d = durationSec, d > 0 else { return nil }
        return "\(d / 60) 分钟"
    }

    /// 日期，如 "06-03"
    var dateText: String? {
        ISODate.shortDate(startTime ?? createdAt)
    }

    /// 状态中文
    var statusText: String {
        switch status {
        case "analyzed": return "已完成"
        case "processing": return "分析中"
        case "uploaded": return "待分析"
        case "error", "failed": return "失败"
        default: return status
        }
    }
}

struct SessionListResponse: Decodable {
    let items: [AnalyzedSession]
    let total: Int
}
