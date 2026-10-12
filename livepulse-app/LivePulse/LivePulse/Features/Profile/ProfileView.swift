//
//  ProfileView.swift
//  LivePulse
//
//  个人中心（"我的" tab，Phase 5）：紫色渐变用户卡 + 菜单 + 退出登录。
//  菜单项为静态入口（功能未接），点了给"开发中"提示，不伪造业务。
//

import SwiftUI

@MainActor
struct ProfileView: View {
    @Environment(AuthStore.self) private var auth

    @State private var comingSoon = false
    @State private var showLogoutConfirm = false

    private struct MenuItem: Identifiable {
        let id = UUID()
        let icon: String
        let title: String
    }

    private let items: [MenuItem] = [
        .init(icon: "person.text.rectangle", title: "个人信息"),
        .init(icon: "crown.fill", title: "我的套餐"),
        .init(icon: "questionmark.circle", title: "使用帮助"),
        .init(icon: "bubble.left.and.bubble.right", title: "意见反馈"),
        .init(icon: "gearshape", title: "设置"),
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: LPSpacing.lg) {
                    userCard

                    VStack(spacing: 0) {
                        ForEach(Array(items.enumerated()), id: \.element.id) { idx, item in
                            Button { comingSoon = true } label: {
                                menuRow(item)
                            }
                            .buttonStyle(.plain)
                            if idx < items.count - 1 {
                                Divider().padding(.leading, 52)
                            }
                        }
                    }
                    .lpCard(padding: 0)

                    Button(role: .destructive) {
                        showLogoutConfirm = true
                    } label: {
                        Text("退出登录")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(LPColor.scoreLow)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(LPColor.card)
                            .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
                            .lpCardShadow()
                    }
                    .buttonStyle(.plain)
                }
                .padding(LPSpacing.md)
            }
            .background(LPColor.bg)
            .navigationTitle("我的")
            .alert("功能开发中", isPresented: $comingSoon) {
                Button("好", role: .cancel) {}
            } message: {
                Text("该功能即将上线，敬请期待。")
            }
            .alert("退出登录", isPresented: $showLogoutConfirm) {
                Button("退出", role: .destructive) { auth.logout() }
                Button("取消", role: .cancel) {}
            } message: {
                Text("确定退出当前账号吗？")
            }
        }
    }

    // MARK: - 用户卡（紫色渐变）

    private var userCard: some View {
        let email = auth.currentUser?.email ?? "LivePulse 用户"
        let initial = String(email.prefix(1)).uppercased()
        return HStack(spacing: LPSpacing.md) {
            EditableAvatar(key: "user/me", initial: initial, size: 64)
                .overlay(Circle().stroke(.white.opacity(0.5), lineWidth: 1))
            VStack(alignment: .leading, spacing: 4) {
                Text(email)
                    .font(.headline)
                    .foregroundStyle(.white)
                    .lineLimit(1)
                Text("每一场直播都有迹可循")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.85))
            }
            Spacer()
        }
        .padding(LPSpacing.lg)
        .frame(maxWidth: .infinity)
        .background(LPGradient.brand)
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.lg, style: .continuous))
        .lpCardShadow()
    }

    private func menuRow(_ item: MenuItem) -> some View {
        HStack(spacing: 14) {
            Image(systemName: item.icon)
                .font(.subheadline)
                .foregroundStyle(LPColor.accent)
                .frame(width: 24)
            Text(item.title)
                .font(.subheadline)
                .foregroundStyle(LPColor.text)
            Spacer()
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(LPColor.textFaint)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .contentShape(Rectangle())
    }
}

#Preview {
    ProfileView().environment(AuthStore())
}
