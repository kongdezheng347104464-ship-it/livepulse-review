//
//  AnalysisReport.swift
//  LivePulse
//
//  AI 分析报告。来自 backend GET /sessions/{id} → data.session.analysis_summary.insight_report。
//  服务端已把洞察解析成人话（text/claim），无需端侧 action_key 映射。
//

import Foundation

// MARK: - 解码路径包装（data.session.analysis_summary.insight_report）

struct SessionDetailEnvelope: Decodable {
    let session: SessionDetailNode
}

struct SessionDetailNode: Decodable {
    let analysisSummary: AnalysisSummaryNode?
    enum CodingKeys: String, CodingKey {
        case analysisSummary = "analysis_summary"
    }
}

struct AnalysisSummaryNode: Decodable {
    let insightReport: AnalysisReport?
    enum CodingKeys: String, CodingKey {
        case insightReport = "insight_report"
    }
}

// MARK: - 报告本体

struct AnalysisReport: Decodable {
    let summary: ReportSummary?
    let insights: [ReportInsight]
}

struct ReportSummary: Decodable {
    let overallScore: Double?
    let engagementScore: Double?
    let conversionScore: Double?
    let contentScore: Double?
    let duration: String?

    enum CodingKeys: String, CodingKey {
        case duration
        case overallScore = "overall_score"
        case engagementScore = "engagement_score"
        case conversionScore = "conversion_score"
        case contentScore = "content_score"
    }
}

struct ReportInsight: Decodable, Identifiable {
    let text: String          // 标题，如 "1:30-3:30 触发购买意向 32 条"
    let claim: String?        // 详情
    let impact: String?       // conversion / engagement / content
    let priority: String?     // high / medium / low
    let score: Double?
    let timeRange: TimeRange?

    var id: String { text + (timeRange.map { "\($0.startSec)" } ?? "") }

    struct TimeRange: Decodable {
        let startSec: Double?
        let endSec: Double?
        enum CodingKeys: String, CodingKey {
            case startSec = "start_sec"
            case endSec = "end_sec"
        }
    }

    enum CodingKeys: String, CodingKey {
        case text, claim, impact, priority, score
        case timeRange = "time_range"
    }

    /// 影响维度中文
    var impactLabel: String {
        switch impact {
        case "conversion": return "转化"
        case "engagement": return "互动"
        case "content": return "内容"
        default: return "亮点"
        }
    }

    /// 起始时间（秒），用于点击跳转
    var startSec: Double? { timeRange?.startSec }
}
