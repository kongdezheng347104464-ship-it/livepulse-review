//
//  AnchorTrend.swift
//  LivePulse
//
//  跨场进步追踪（B4 差异化武器）。backend GET /anchors/{id}/trend。
//

import Foundation

struct AnchorTrend: Decodable {
    let anchorName: String?
    let totalSessions: Int
    let items: [TrendPoint]
    let minDurationMin: Int?

    enum CodingKeys: String, CodingKey {
        case items
        case anchorName = "anchor_name"
        case totalSessions = "total_sessions"
        case minDurationMin = "min_duration_min"
    }
}

struct TrendPoint: Decodable, Identifiable {
    let label: String
    let timestamp: String?
    let composite: Double?
    let engagement: Double?
    let conversion: Double?
    let content: Double?

    // 用 timestamp + label 做稳定 id（同日多场 label 会重复）
    var id: String { (timestamp ?? "") + label }
}
