//
//  LoginView.swift
//  LivePulse
//
//  登录页 v2：紫色渐变全屏 + 品牌 logo + 玻璃感输入框 + 白色主按钮。
//  对齐 product-design-board 参考图。
//

import SwiftUI

struct LoginView: View {
    @Environment(AuthStore.self) private var auth

    @State private var email = ""
    @State private var password = ""
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showRegister = false
    @State private var showPassword = false

    var body: some View {
        NavigationStack {
            ZStack {
                LPGradient.authBackground.ignoresSafeArea()

                ScrollView {
                    VStack(spacing: LPSpacing.lg) {
                        brand
                            .padding(.top, 60)

                        VStack(spacing: LPSpacing.md) {
                            glassField(icon: "envelope.fill", placeholder: "邮箱 / 手机号") {
                                TextField("", text: $email, prompt: prompt("邮箱 / 手机号"))
                                    .textContentType(.emailAddress)
                                    .keyboardType(.emailAddress)
                                    .textInputAutocapitalization(.never)
                                    .autocorrectionDisabled()
                            }

                            glassField(icon: "lock.fill", placeholder: "密码", trailing: passwordToggle) {
                                Group {
                                    if showPassword {
                                        TextField("", text: $password, prompt: prompt("密码"))
                                    } else {
                                        SecureField("", text: $password, prompt: prompt("密码"))
                                    }
                                }
                                .textContentType(.password)
                            }
                        }

                        HStack {
                            Spacer()
                            Text("忘记密码？")
                                .font(.footnote)
                                .foregroundStyle(.white.opacity(0.85))
                        }

                        if let errorMessage {
                            Text(errorMessage)
                                .font(.footnote)
                                .foregroundStyle(.white)
                                .padding(.horizontal, 12).padding(.vertical, 8)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(.white.opacity(0.18))
                                .clipShape(RoundedRectangle(cornerRadius: LPRadius.sm, style: .continuous))
                        }

                        submitButton
                        footer
                    }
                    .padding(LPSpacing.lg)
                }
            }
            .navigationDestination(isPresented: $showRegister) {
                RegisterView()
            }
        }
    }

    // MARK: - 品牌头

    private var brand: some View {
        VStack(spacing: LPSpacing.md) {
            HStack(spacing: 10) {
                Image("AppLogo")
                    .resizable().scaledToFit()
                    .frame(width: 40, height: 40)
                Text("LivePulse")
                    .font(.system(size: 26, weight: .bold))
                    .foregroundStyle(.white)
            }
            VStack(spacing: LPSpacing.xs) {
                Text("欢迎回来")
                    .font(.system(size: 28, weight: .bold))
                    .foregroundStyle(.white)
                Text("登录你的 LivePulse 账号，继续复盘每一场直播")
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.85))
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - 玻璃输入框

    private func prompt(_ s: String) -> Text {
        Text(s).foregroundColor(.white.opacity(0.6))
    }

    private func glassField<Content: View>(
        icon: String,
        placeholder: String,
        trailing: AnyView? = nil,
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.8))
                .frame(width: 20)
            content()
                .foregroundStyle(.white)
                .tint(.white)
            if let trailing { trailing }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .background(.white.opacity(0.15))
        .clipShape(RoundedRectangle(cornerRadius: LPRadius.md, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: LPRadius.md, style: .continuous)
                .stroke(.white.opacity(0.25), lineWidth: 1)
        )
    }

    private var passwordToggle: AnyView {
        AnyView(
            Button {
                showPassword.toggle()
            } label: {
                Image(systemName: showPassword ? "eye.slash.fill" : "eye.fill")
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.7))
            }
        )
    }

    // MARK: - 主按钮（白底紫字，反衬在渐变上）

    private var submitButton: some View {
        Button(action: handleLogin) {
            HStack {
                if isLoading { ProgressView().tint(LPColor.accent) }
                Text(isLoading ? "登录中…" : "登录")
                    .font(.headline)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 15)
            .background(.white)
            .foregroundStyle(LPColor.accent)
            .clipShape(RoundedRectangle(cornerRadius: LPRadius.md, style: .continuous))
            .lpSoftShadow()
        }
        .disabled(isLoading || email.isEmpty || password.isEmpty)
        .opacity((email.isEmpty || password.isEmpty) ? 0.6 : 1)
    }

    private var footer: some View {
        HStack(spacing: 4) {
            Text("还没有账号？")
                .foregroundStyle(.white.opacity(0.85))
            Button("邀请码注册") { showRegister = true }
                .foregroundStyle(.white)
                .fontWeight(.semibold)
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity)
        .padding(.top, LPSpacing.sm)
    }

    // MARK: - 动作

    private func handleLogin() {
        errorMessage = nil
        isLoading = true
        Task {
            defer { isLoading = false }
            do {
                try await auth.login(email: email, password: password)
            } catch {
                errorMessage = (error as? APIError)?.errorDescription ?? "登录失败"
            }
        }
    }
}

#Preview {
    LoginView()
        .environment(AuthStore())
}
