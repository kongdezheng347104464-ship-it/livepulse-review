//
//  Dashboard.swift
//  LivePulse
//
//  首页"复盘中控台"聚合（GET /dashboard，后端一次算好）。
//

import Foundation

struct DashboardResponse: Decodable {
    let inProgress: [InProgressSession]
    let thisMonth: ThisMonth
    let exportableHighlights: Int
    let recentReports: [RecentReport]
    let frequentAnchors: [FrequentAnchor]

    enum CodingKeys: String, CodingKey {
        case inProgress = "in_progress"
        case thisMonth = "this_month"
        case exportableHighlights = "exportable_highlights"
        case recentReports = "recent_reports"
        case frequentAnchors = "frequent_anchors"
    }

    struct ThisMonth: Decodable {
        let reviewCount: Int
        let avgScore: Double?
        enum CodingKeys: String, CodingKey {
            case reviewCount = "review_count"
            case avgScore = "avg_score"
        }
    }
}

struct InProgressSession: Decodable, Identifiable {
    let sessionId: String
    let title: String?
    let anchorName: String?
    let status: String
    let stage: String
    var id: String { sessionId }

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case title, status, stage
        case anchorName = "anchor_name"
    }
}

struct RecentReport: Decodable, Identifiable {
    let sessionId: String
    let title: String?
    let anchorName: String?
    let date: String?
    let compositeScore: Double?
    var id: String { sessionId }

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case title, date
        case anchorName = "anchor_name"
        case compositeScore = "composite_score"
    }

    /// 转成 AnalyzedSession 以便导航进工作台（只用 id/status/title 即可加载）
    var asSession: AnalyzedSession {
        AnalyzedSession(
            id: sessionId, anchorId: nil, anchorName: anchorName,
            title: title, durationSec: nil, compositeScore: compositeScore,
            status: "analyzed", scores: nil, startTime: date, createdAt: nil
        )
    }
}

struct FrequentAnchor: Decodable, Identifiable {
    let id: String
    let name: String
    let avatarUrl: String?
    let sessionCount: Int

    enum CodingKeys: String, CodingKey {
        case id, name
        case avatarUrl = "avatar_url"
        case sessionCount = "session_count"
    }

    /// 转成 Anchor 以便导航进场次页
    var asAnchor: Anchor {
        Anchor(
            id: id, name: name, platform: nil,
            sessionCount: sessionCount, lastSessionAt: nil, avatarUrl: avatarUrl
        )
    }
}
