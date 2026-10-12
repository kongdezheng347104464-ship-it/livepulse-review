//
//  AuthStore.swift
//  LivePulse
//
//  全局登录状态。用 @Observable（iOS 17+）。
//  对应 web frontend_v2 的 authStore（Zustand）。
//

import Foundation
import Observation

@MainActor
@Observable
final class AuthStore {
    /// 当前登录用户，nil = 未登录
    var currentUser: User?

    /// 启动时是否还在校验 token（决定显示 splash 还是登录页/主页）
    var isBootstrapping = true

    /// 是否已登录
    var isLoggedIn: Bool { currentUser != nil }

    /// nonisolated init：让 `@State private var auth = AuthStore()` 能在
    /// SwiftUI App 的 nonisolated 上下文里初始化。所有 @MainActor 工作（接 401
    /// 回调、拉 /auth/me）都放到 bootstrap() 里做。
    nonisolated init() {}

    // MARK: - 启动校验

    /// app 启动时调用：接 401 回调 + 有 token 就拉 /auth/me 验证，无效则清掉
    func bootstrap() async {
        // 把 401 回调接到 logout，APIClient 任何请求 401 都会触发登出
        APIClient.shared.onUnauthorized = { [weak self] in
            self?.logout()
        }
        defer { isBootstrapping = false }
        guard KeychainStore.loadToken() != nil else {
            return  // 无 token，停在登录页
        }
        do {
            let me: User = try await APIClient.shared.get("/auth/me")
            currentUser = me
        } catch {
            // token 失效/网络问题 → 清掉，回登录页
            KeychainStore.deleteToken()
            currentUser = nil
        }
    }

    // MARK: - 登录 / 注册 / 登出

    func login(email: String, password: String) async throws {
        let req = LoginRequest(email: email, password: password)
        let token: AuthTokenResponse = try await APIClient.shared.post("/auth/login", body: req)
        KeychainStore.saveToken(token.accessToken)

        let me: User = try await APIClient.shared.get("/auth/me")
        currentUser = me
    }

    func register(email: String, password: String, invitationCode: String) async throws {
        let req = RegisterRequest(email: email, password: password, invitationCode: invitationCode)
        let token: AuthTokenResponse = try await APIClient.shared.post("/auth/register", body: req)
        KeychainStore.saveToken(token.accessToken)

        let me: User = try await APIClient.shared.get("/auth/me")
        currentUser = me
    }

    func logout() {
        KeychainStore.deleteToken()
        currentUser = nil
    }
}
