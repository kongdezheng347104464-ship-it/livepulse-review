//
//  MainTabView.swift
//  LivePulse
//
//  登录后的主容器（Phase 5）：底部 Tab 导航。
//  首页（主播）/ 场次（全部场次）/ 我的。
//  注：参考图里的"工作台"是上下文相关页（需先选场次），不作为全局 Tab，
//  保留为 首页/场次 → 点进去的下钻页。
//

import SwiftUI

struct MainTabView: View {
    var body: some View {
        TabView {
            AnchorListView()
                .tabItem { Label("首页", systemImage: "house.fill") }

            AnchorTabView()
                .tabItem { Label("主播", systemImage: "dot.radiowaves.left.and.right") }

            RecentSessionsView()
                .tabItem { Label("场次", systemImage: "chart.bar.doc.horizontal.fill") }

            ProfileView()
                .tabItem { Label("我的", systemImage: "person.fill") }
        }
        .tint(LPColor.accent)
    }
}

#Preview {
    MainTabView().environment(AuthStore())
}
