//
//  LivePulseApp.swift
//  LivePulse
//
//  Created by Andrew Zhang on 2026/6/8.
//

import SwiftUI
import SwiftData

@main
struct LivePulseApp: App {
    @State private var auth = AuthStore()

    /// SwiftData 容器：本地存复盘场次 + 笔记。
    /// 初始化失败不崩溃,分级降级:正常 → 删损坏 store 重建 → 退回内存态。
    /// (笔记已后端同步,本地缓存丢失可恢复;保证 App 始终能启动)
    let modelContainer: ModelContainer = {
        let schema = Schema([ReviewSession.self, Note.self])
        let config = ModelConfiguration(schema: schema, isStoredInMemoryOnly: false)

        // 1) 正常持久化容器
        if let container = try? ModelContainer(for: schema, configurations: [config]) {
            return container
        }
        // 2) 本地 store 损坏 / migration 不兼容 → 删掉重建一次
        Self.deleteDefaultStore()
        if let container = try? ModelContainer(for: schema, configurations: [config]) {
            return container
        }
        // 3) 仍失败 → 退回内存态(本地不持久化,但 App 不崩,可正常进入)
        let memoryConfig = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
        return try! ModelContainer(for: schema, configurations: [memoryConfig])
    }()

    /// 删除默认 SwiftData store(含 -shm / -wal 边车文件)
    private static func deleteDefaultStore() {
        guard let appSupport = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first else { return }
        for name in ["default.store", "default.store-shm", "default.store-wal"] {
            try? FileManager.default.removeItem(at: appSupport.appendingPathComponent(name))
        }
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(auth)
        }
        .modelContainer(modelContainer)
    }
}
