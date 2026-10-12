//
//  RangeAnalysis.swift
//  LivePulse
//
//  AI 选段点评（差异化武器）。用户选话术段 → POST /sessions/{id}/range-analysis
//  → Claude/Qwen 按主播类型自适应点评（ADR-0003：教学型不给带货建议）。
//

import Foundation

struct RangeAnalysisRequest: Encodable {
    let segmentIds: [String]
    enum CodingKeys: String, CodingKey {
        case segmentIds = "segment_ids"
    }
}

/// POST 返回的 data 层（只取 result，其他字段忽略）
struct RangeAnalysisResponse: Decodable {
    let result: RangeAnalysisResult
}

struct RangeAnalysisResult: Decodable {
    let streamerType: String?
    let summary: String?
    let highlight: String?
    let findings: [Finding]

    enum CodingKeys: String, CodingKey {
        case summary, highlight, findings
        case streamerType = "streamer_type"
    }

    // 兼容：findings 缺失时为空数组
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        streamerType = try c.decodeIfPresent(String.self, forKey: .streamerType)
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        highlight = try c.decodeIfPresent(String.self, forKey: .highlight)
        findings = (try? c.decode([Finding].self, forKey: .findings)) ?? []
    }
}

struct Finding: Decodable, Identifiable {
    let dimension: String?
    let moment: String?
    let observation: String?
    let problem: String?
    let action: String?

    var id: String { (dimension ?? "") + "|" + (moment ?? "") }
}

// MARK: - 历史点评（GET /sessions/{id}/range-analyses）

struct RangeAnalysisListResponse: Decodable {
    let items: [RangeAnalysisRecord]
}

struct RangeAnalysisRecord: Decodable, Identifiable, Hashable {
    let id: String
    let result: RangeAnalysisResult
    let rangeStartSec: Double?
    let rangeEndSec: Double?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, result
        case rangeStartSec = "range_start_sec"
        case rangeEndSec = "range_end_sec"
        case createdAt = "created_at"
    }

    static func == (l: RangeAnalysisRecord, r: RangeAnalysisRecord) -> Bool { l.id == r.id }
    func hash(into h: inout Hasher) { h.combine(id) }

    /// 时间范围显示，如 "0:00 - 1:30"
    var rangeText: String {
        func fmt(_ v: Double?) -> String {
            let t = Int((v ?? 0).rounded()); return String(format: "%d:%02d", t / 60, t % 60)
        }
        return "\(fmt(rangeStartSec)) - \(fmt(rangeEndSec))"
    }
}
