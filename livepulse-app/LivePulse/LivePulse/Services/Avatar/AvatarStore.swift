//
//  AvatarStore.swift
//  LivePulse
//
//  本地头像存储（方案 A）：选图存到 app 沙盒，按 key 读写。
//  key 形如 "anchors/{id}" / "user/me"。
//  落盘：Application Support/Avatars/{key}.jpg
//
//  做成 @Observable + 内存缓存：改头像后所有显示头像的视图即时刷新
//  （SwiftUI 不会自动感知文件变化）。未来接后端 avatar_url 时，
//  只需把本地图上传回填 URL，显示层不用改。
//

import SwiftUI
import UIKit

@Observable
@MainActor
final class AvatarStore {
    static let shared = AvatarStore()

    /// 每次改头像自增，驱动观察它的视图刷新
    private(set) var version = 0

    /// 内存缓存（不参与 observation，避免读取时触发刷新循环）
    @ObservationIgnored private var cache: [String: UIImage] = [:]

    private init() {}

    /// 读头像（先内存缓存，再磁盘）。无则 nil。
    func image(forKey key: String) -> UIImage? {
        if let img = cache[key] { return img }
        guard let url = fileURL(forKey: key),
              let data = try? Data(contentsOf: url),
              let img = UIImage(data: data) else { return nil }
        cache[key] = img
        return img
    }

    /// 存头像（aspect-fill 裁方 + 缩到 240px + jpeg）。
    func save(_ image: UIImage, forKey key: String) {
        let thumb = image.lp_squareThumbnail(240)
        cache[key] = thumb
        if let url = fileURL(forKey: key, createDir: true),
           let data = thumb.jpegData(compressionQuality: 0.85) {
            try? data.write(to: url)
        }
        version += 1
    }

    func delete(forKey key: String) {
        cache[key] = nil
        if let url = fileURL(forKey: key) {
            try? FileManager.default.removeItem(at: url)
        }
        version += 1
    }

    private func fileURL(forKey key: String, createDir: Bool = false) -> URL? {
        guard let base = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first else { return nil }
        let url = base.appendingPathComponent("Avatars/\(key).jpg")
        if createDir {
            try? FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
        }
        return url
    }
}

extension UIImage {
    /// aspect-fill 居中裁成正方形并缩放到边长 side（保方向、不变形）。
    func lp_squareThumbnail(_ side: CGFloat) -> UIImage {
        let target = CGSize(width: side, height: side)
        let scale = max(side / size.width, side / size.height)
        let drawSize = CGSize(width: size.width * scale, height: size.height * scale)
        let origin = CGPoint(x: (side - drawSize.width) / 2, y: (side - drawSize.height) / 2)
        let renderer = UIGraphicsImageRenderer(size: target)
        return renderer.image { _ in
            self.draw(in: CGRect(origin: origin, size: drawSize))
        }
    }
}
