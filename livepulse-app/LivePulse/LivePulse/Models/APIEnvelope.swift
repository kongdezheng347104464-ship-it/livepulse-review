//
//  APIEnvelope.swift
//  LivePulse
//
//  Backend 统一响应信封：{ code, data, message }。
//  code == 0 表示成功，data 是真正的 payload。
//

import Foundation

/// 通用响应信封。T 是 data 字段的真实类型。
struct APIEnvelope<T: Decodable>: Decodable {
    let code: Int
    let data: T?
    let message: String?
}

/// 后端返回的错误体（HTTP 4xx/5xx 时的 detail 字段）。
/// FastAPI 默认 { "detail": "..." } 或 { "detail": { ... } }
struct APIErrorBody: Decodable {
    let detail: APIErrorDetail?
}

/// detail 可能是字符串，也可能是结构化对象（如 409 anchor 重名）
enum APIErrorDetail: Decodable {
    case message(String)
    case structured([String: String])

    var displayText: String {
        switch self {
        case .message(let s): return s
        case .structured(let dict): return dict["message"] ?? "请求失败"
        }
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let s = try? container.decode(String.self) {
            self = .message(s)
        } else if let dict = try? container.decode([String: String].self) {
            self = .structured(dict)
        } else {
            self = .message("请求失败")
        }
    }
}

/// API 层抛出的错误
enum APIError: LocalizedError {
    case invalidResponse
    case unauthorized                 // 401，触发登出
    case server(status: Int, message: String)
    case decoding(Error)
    case transport(Error)             // 网络层（无网、超时等）

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "服务器响应异常"
        case .unauthorized:
            return "登录已过期，请重新登录"
        case .server(_, let message):
            return message
        case .decoding:
            return "数据解析失败"
        case .transport(let err):
            return err.localizedDescription
        }
    }
}
