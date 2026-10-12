//
//  User.swift
//  LivePulse
//
//  用户模型 + auth 请求/响应 DTO。对应 backend app/models/schemas/auth.py。
//

import Foundation

/// 当前登录用户（对应 backend /auth/me 返回）
struct User: Codable, Identifiable, Equatable {
    let id: String
    let email: String
    let role: String          // "user" / "admin"
    let isActive: Bool

    var isAdmin: Bool { role == "admin" }

    enum CodingKeys: String, CodingKey {
        case id, email, role
        case isActive = "is_active"
    }
}

// MARK: - 请求体

struct LoginRequest: Encodable {
    let email: String
    let password: String
}

struct RegisterRequest: Encodable {
    let email: String
    let password: String
    let invitationCode: String

    enum CodingKeys: String, CodingKey {
        case email, password
        case invitationCode = "invitation_code"
    }
}

// MARK: - 响应体

/// 登录/注册成功返回的 token（对应 backend { access_token, token_type }）
struct AuthTokenResponse: Decodable {
    let accessToken: String
    let tokenType: String?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case tokenType = "token_type"
    }
}
