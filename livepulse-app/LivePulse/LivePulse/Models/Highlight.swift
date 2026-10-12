//
//  Highlight.swift
//  LivePulse
//
//  高光时刻（Sprint 6B）：后端 GET /sessions/{id}/highlights 返回的可分享高光，
//  以及 POST .../export 导出片段的响应。高光来自 insight_report（score>=60），
//  不是本地识别。详见 docs/05-sprint6-highlights.md。
//

import Foundation

struct Highlight: Decodable, Identifiable {
    let id: String            // = start_sec（后端合成的稳定 id）
    let startSec: Double
    let endSec: Double
    let score: Double
    let type: String          // conversion_spike / engagement_peak / insight
    let reason: String        // 如 "4:00-5:30 触发购买意向 78 条"

    enum CodingKeys: String, CodingKey {
        case id, score, type, reason
        case startSec = "start_sec"
        case endSec = "end_sec"
    }
}

/// GET /sessions/{id}/highlights 的 data 层（envelope 已被 APIClient 解包）。
/// items 为空是正常态（约 36% 的场），UI 渲染空态而非报错。
struct HighlightsResponse: Decodable {
    let items: [Highlight]
}

/// POST /sessions/{id}/highlights/{id}/export 的 data 层。
struct ClipExportResponse: Decodable {
    let clipUrl: String
    let expiresIn: Int

    enum CodingKeys: String, CodingKey {
        case clipUrl = "clip_url"
        case expiresIn = "expires_in"
    }
}
