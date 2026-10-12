//
//  Anchor.swift
//  LivePulse
//
//  主播（后端实体）。对应 backend GET /anchors/。
//

import Foundation

struct Anchor: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let platform: String?
    let sessionCount: Int
    let lastSessionAt: String?
    let avatarUrl: String?

    enum CodingKeys: String, CodingKey {
        case id, name, platform
        case sessionCount = "session_count"
        case lastSessionAt = "last_session_at"
        case avatarUrl = "avatar_url"
    }

    /// 头像首字
    var initial: String { String(name.prefix(1)) }

    /// 远程头像 URL（后端 avatar_url；本地头像优先，见 AvatarCircle）
    var avatarURL: URL? { avatarUrl.flatMap { URL(string: $0) } }

    /// 最近场次日期友好显示，如 "06-03"
    var lastSessionText: String? {
        ISODate.shortDate(lastSessionAt)
    }
}

struct AnchorListResponse: Decodable {
    let items: [Anchor]
    let total: Int
}

// MARK: - ISO 日期工具

enum ISODate {
    /// "2026-06-03T10:14:35Z" → "06-03"
    static func shortDate(_ iso: String?) -> String? {
        guard let iso else { return nil }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = f.date(from: iso) ?? {
            let f2 = ISO8601DateFormatter()
            f2.formatOptions = [.withInternetDateTime]
            return f2.date(from: iso)
        }()
        guard let date else { return nil }
        let out = DateFormatter()
        out.dateFormat = "MM-dd"
        return out.string(from: date)
    }
}
