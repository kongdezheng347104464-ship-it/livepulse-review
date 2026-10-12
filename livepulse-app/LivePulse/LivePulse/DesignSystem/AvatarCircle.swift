//
//  AvatarCircle.swift
//  LivePulse
//
//  头像组件。显示优先级：本地头像 → 远程 avatar_url(以后) → 首字母占位。
//  AvatarCircle = 只读显示；EditableAvatar = 带相机角标，点击换图(PhotosPicker)。
//

import SwiftUI
import PhotosUI

/// 只读头像圆
@MainActor
struct AvatarCircle: View {
    let key: String              // AvatarStore key, 如 "anchors/{id}" / "user/me"
    let initial: String
    var remoteURL: URL? = nil    // 未来后端 avatar_url
    var size: CGFloat = 46

    var body: some View {
        Group {
            if let img = currentImage() {
                Image(uiImage: img).resizable().scaledToFill()
            } else if let remoteURL {
                AsyncImage(url: remoteURL) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFill()
                    } else {
                        placeholder
                    }
                }
            } else {
                placeholder
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
    }

    /// 在 body 内调用 → version 访问被 observation 追踪 → 改头像即时刷新
    private func currentImage() -> UIImage? {
        _ = AvatarStore.shared.version
        return AvatarStore.shared.image(forKey: key)
    }

    private var placeholder: some View {
        Text(initial)
            .font(.system(size: size * 0.4, weight: .bold))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(LPGradient.primary)
    }
}

/// 可编辑头像：点击弹相册选图，存到本地 AvatarStore
@MainActor
struct EditableAvatar: View {
    let key: String
    let initial: String
    var remoteURL: URL? = nil
    var size: CGFloat = 72

    @State private var photoItem: PhotosPickerItem?

    var body: some View {
        PhotosPicker(selection: $photoItem, matching: .images, photoLibrary: .shared()) {
            AvatarCircle(key: key, initial: initial, remoteURL: remoteURL, size: size)
                .overlay(alignment: .bottomTrailing) {
                    Image(systemName: "camera.fill")
                        .font(.system(size: size * 0.16))
                        .foregroundStyle(.white)
                        .padding(size * 0.1)
                        .background(LPColor.accent)
                        .clipShape(Circle())
                        .overlay(Circle().stroke(.white, lineWidth: 2))
                }
        }
        .onChange(of: photoItem) { _, item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self),
                   let img = UIImage(data: data) {
                    AvatarStore.shared.save(img, forKey: key)
                }
                photoItem = nil
            }
        }
    }
}
