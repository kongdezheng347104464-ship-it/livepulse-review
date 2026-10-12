//
//  RegisterView.swift
//  LivePulse
//
//  注册页 v2：浅蓝紫底 + 玻璃感白卡 + 紫色渐变主按钮（需邀请码）。
//  对齐 product-design-board 参考图。
//

import SwiftUI

struct RegisterView: View {
    @Environment(AuthStore.self) private var auth
    @Environment(\.dismiss) private var dismiss

    @State private var email = ""
    @State private var password = ""
    @State private var invitationCode = ""
    @State private var isLoading = false
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: LPSpacing.lg) {
                header

                VStack(spacing: LPSpacing.md) {
                    labeledField(title: "邮箱") {
                        TextField("you@livepulse.app", text: $email)
                            .textContentType(.emailAddress)
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }

                    labeledField(title: "密码（至少 8 位，含字母 + 数字）") {
                        SecureField("••••••••", text: $password)
                            .textContentType(.newPassword)
                    }

                    labeledField(title: "邀请码") {
                        TextField("16 位邀请码", text: $invitationCode)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }

                    if let errorMessage {
                        Text(errorMessage)
                            .font(.footnote)
                            .foregroundStyle(LPColor.scoreLow)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    Button(action: handleRegister) {
                        HStack {
                            if isLoading { ProgressView().tint(.white) }
                            Text(isLoading ? "注册中…" : "注册")
                        }
                    }
                    .buttonStyle(LPPrimaryButtonStyle())
                    .disabled(isLoading || !canSubmit)
                    .opacity(canSubmit ? 1 : 0.5)
                    .padding(.top, LPSpacing.xs)
                }
                .lpCard(padding: 18)
            }
            .padding(LPSpacing.lg)
        }
        .background(LPColor.bg)
        .navigationTitle("创建账号")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: - 子视图

    private var header: some View {
        VStack(alignment: .leading, spacing: LPSpacing.xs) {
            Text("创建账号")
                .font(.system(size: 28, weight: .bold))
                .foregroundStyle(LPColor.text)
            Text("输入邀请码，开启你的复盘之旅")
                .font(.subheadline)
                .foregroundStyle(LPColor.textMute)
        }
        .padding(.top, LPSpacing.md)
    }

    private func labeledField<Content: View>(
        title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: LPSpacing.sm) {
            Text(title)
                .font(.footnote.weight(.medium))
                .foregroundStyle(LPColor.textMid)
            content()
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(LPColor.bg)
                .clipShape(RoundedRectangle(cornerRadius: LPRadius.sm, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: LPRadius.sm, style: .continuous)
                        .stroke(LPColor.border, lineWidth: 1)
                )
        }
    }

    private var canSubmit: Bool {
        !email.isEmpty && password.count >= 8 && !invitationCode.isEmpty
    }

    // MARK: - 动作

    private func handleRegister() {
        errorMessage = nil
        isLoading = true
        Task {
            defer { isLoading = false }
            do {
                try await auth.register(
                    email: email,
                    password: password,
                    invitationCode: invitationCode
                )
            } catch {
                errorMessage = (error as? APIError)?.errorDescription ?? "注册失败"
            }
        }
    }
}

#Preview {
    NavigationStack {
        RegisterView()
            .environment(AuthStore())
    }
}
