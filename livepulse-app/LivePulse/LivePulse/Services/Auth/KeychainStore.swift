//
//  KeychainStore.swift
//  LivePulse
//
//  JWT token 安全存储。用 iOS Keychain（比 UserDefaults 安全）。
//

import Foundation
import Security

/// 极简 Keychain 封装：只存一个 string（JWT）。
enum KeychainStore {
    private static let service = "app.livepulse.auth"
    private static let account = "jwt_token"

    /// 存 token（已存在则覆盖）
    static func saveToken(_ token: String) {
        let data = Data(token.utf8)

        // 先删旧的，避免 duplicate
        let deleteQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(deleteQuery as CFDictionary)

        let addQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: data,
            // 仅本设备解锁后可访问，不同步到 iCloud
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        SecItemAdd(addQuery as CFDictionary, nil)
    }

    /// 读 token，没有返回 nil
    static func loadToken() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess,
              let data = result as? Data,
              let token = String(data: data, encoding: .utf8)
        else {
            return nil
        }
        return token
    }

    /// 删除 token（登出用）
    static func deleteToken() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
