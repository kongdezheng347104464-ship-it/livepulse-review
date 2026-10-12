//
//  RootView.swift
//  LivePulse
//
//  根视图：根据登录状态切换 splash / 登录页 / 主页。
//

import SwiftUI

struct RootView: View {
    @Environment(AuthStore.self) private var auth

    var body: some View {
        Group {
            if auth.isBootstrapping {
                SplashView()
            } else if auth.isLoggedIn {
                MainTabView()
            } else {
                LoginView()
            }
        }
        .task {
            // app 启动时校验本地 token
            await auth.bootstrap()
        }
    }
}

/// 启动校验期间的占位
struct SplashView: View {
    var body: some View {
        VStack(spacing: LPSpacing.md) {
            ProgressView()
                .tint(LPColor.accent)
            Text("LivePulse")
                .font(.title2.weight(.bold))
                .foregroundStyle(LPColor.accent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(LPColor.bg)
    }
}

#Preview {
    RootView()
        .environment(AuthStore())
}
