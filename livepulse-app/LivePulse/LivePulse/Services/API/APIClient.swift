//
//  APIClient.swift
//  LivePulse
//
//  统一网络层：URLSession + async/await。
//  - 自动注入 Bearer token
//  - 自动解包 backend envelope { code, data, message }
//  - 401 自动触发登出回调
//

import Foundation

@MainActor
final class APIClient {
    static let shared = APIClient()

    private let session: URLSession
    private let decoder: JSONDecoder

    /// 401 时调用（由 AuthStore 注入，触发清 token + 跳登录）
    var onUnauthorized: (() -> Void)?

    private init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = APIConfig.requestTimeout
        self.session = URLSession(configuration: config)
        self.decoder = JSONDecoder()
    }

    // MARK: - 公开方法

    /// GET 请求，返回解包后的 data 层
    func get<T: Decodable>(_ path: String, query: [String: String] = [:]) async throws -> T {
        let request = try makeRequest(path: path, method: "GET", query: query, body: Optional<Data>.none)
        return try await send(request)
    }

    /// POST 请求，body 是可编码对象
    func post<T: Decodable, B: Encodable>(_ path: String, body: B) async throws -> T {
        let bodyData = try JSONEncoder().encode(body)
        let request = try makeRequest(path: path, method: "POST", query: [:], body: bodyData)
        return try await send(request)
    }

    /// POST 无 body
    func post<T: Decodable>(_ path: String) async throws -> T {
        let request = try makeRequest(path: path, method: "POST", query: [:], body: Optional<Data>.none)
        return try await send(request)
    }

    /// PATCH 请求（只判成功，不解析返回体）
    func patchVoid<B: Encodable>(_ path: String, body: B) async throws {
        let bodyData = try JSONEncoder().encode(body)
        let request = try makeRequest(path: path, method: "PATCH", query: [:], body: bodyData)
        try await sendVoid(request)
    }

    /// DELETE 请求（后端返回 204，无 body）
    func delete(_ path: String) async throws {
        let request = try makeRequest(path: path, method: "DELETE", query: [:], body: Optional<Data>.none)
        try await sendVoid(request)
    }

    // MARK: - 内部实现

    /// 发请求只判状态码，不解析 body（用于 PATCH/DELETE 等无 body 或不关心 body 的请求）
    private func sendVoid(_ request: URLRequest) async throws {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        if http.statusCode == 401 {
            onUnauthorized?()
            throw APIError.unauthorized
        }
        guard (200...299).contains(http.statusCode) else {
            let message = (try? decoder.decode(APIErrorBody.self, from: data))?
                .detail?.displayText ?? "请求失败（\(http.statusCode)）"
            throw APIError.server(status: http.statusCode, message: message)
        }
    }

    private func makeRequest(
        path: String,
        method: String,
        query: [String: String],
        body: Data?
    ) throws -> URLRequest {
        // 拼 URL（path 形如 "/auth/login"）
        var components = URLComponents(
            url: APIConfig.baseURL.appendingPathComponent(path),
            resolvingAgainstBaseURL: false
        )
        if !query.isEmpty {
            components?.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        guard let url = components?.url else {
            throw APIError.invalidResponse
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        // 注入 Bearer token
        if let token = KeychainStore.loadToken() {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        if let body = body {
            request.httpBody = body
        }
        return request
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.transport(error)
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }

        // 401：触发登出（除登录/注册路径外）
        if http.statusCode == 401 {
            let path = request.url?.path ?? ""
            if !path.contains("/auth/login") && !path.contains("/auth/register") {
                onUnauthorized?()
            }
            throw APIError.unauthorized
        }

        // 其他错误：尝试解析 detail 文案
        guard (200...299).contains(http.statusCode) else {
            let message = (try? decoder.decode(APIErrorBody.self, from: data))?
                .detail?.displayText ?? "请求失败（\(http.statusCode)）"
            throw APIError.server(status: http.statusCode, message: message)
        }

        // 成功：条件解包（跟 web client.js 一致）
        // backend 有的接口包 envelope {code,data,message}（如 /sessions/），
        // 有的直接返回裸对象（如 /auth/login、/auth/me）。
        // 检测到顶层有 code 字段才解包 envelope，否则整体当 T 解。
        do {
            if let probe = try? decoder.decode(EnvelopeProbe.self, from: data),
               probe.code != nil {
                let envelope = try decoder.decode(APIEnvelope<T>.self, from: data)
                guard let payload = envelope.data else {
                    throw APIError.invalidResponse
                }
                return payload
            } else {
                return try decoder.decode(T.self, from: data)
            }
        } catch let decodingError {
            throw APIError.decoding(decodingError)
        }
    }
}

/// 探测响应是否为 envelope（只读顶层 code 字段）
private struct EnvelopeProbe: Decodable {
    let code: Int?
}
