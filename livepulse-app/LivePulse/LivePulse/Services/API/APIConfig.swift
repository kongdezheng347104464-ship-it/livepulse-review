//
//  APIConfig.swift
//  LivePulse
//
//  API 环境配置。dev 走本地 backend，prod 走线上。
//  编译时根据 DEBUG 自动选，上线无需改代码。
//

import Foundation

enum APIConfig {
    /// API 根地址（含 /api/v1 前缀）
    static var baseURL: URL {
        #if DEBUG
        // 本地联调：模拟器访问 Mac 的 localhost 直接用 127.0.0.1
        // （真机调试时要改成 Mac 的局域网 IP，如 http://192.168.x.x:8004）
        return URL(string: "http://127.0.0.1:8000/api/v1")!
        #else
        // 生产：等 example.com 备案上线后生效
        return URL(string: "https://example.com/api/v1")!
        #endif
    }

    /// 请求超时（秒）。普通请求 30s；上传单独设更长。
    static let requestTimeout: TimeInterval = 30
}
