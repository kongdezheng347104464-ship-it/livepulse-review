//
//  VideoAsset.swift
//  LivePulse
//
//  用户从相册选中的视频 + 解析出的元数据。
//

import Foundation
import AVFoundation

/// 选中的本地视频及其元数据
struct VideoAsset: Identifiable {
    let id = UUID()
    /// 本地文件 URL（PHPicker 拷贝到 app 临时目录后的路径）
    let url: URL
    /// 时长（秒）
    let durationSec: Double
    /// 像素宽
    let width: Int
    /// 像素高
    let height: Int
    /// 文件体积（字节）
    let fileSizeBytes: Int64
    /// 视频编码（如 "hvc1"/HEVC、"avc1"/H.264）
    let codec: String

    /// 是否竖屏（抖音手机直播）
    var isPortrait: Bool { height > width }

    /// 时长友好显示，如 "1:15:30" / "13:20"
    var durationText: String {
        let total = Int(durationSec.rounded())
        let h = total / 3600
        let m = (total % 3600) / 60
        let s = total % 60
        if h > 0 {
            return String(format: "%d:%02d:%02d", h, m, s)
        }
        return String(format: "%d:%02d", m, s)
    }

    /// 分辨率显示，如 "1080 × 1920"
    var resolutionText: String { "\(width) × \(height)" }

    /// 体积友好显示，如 "1.5 GB" / "820 MB"
    var fileSizeText: String {
        let mb = Double(fileSizeBytes) / 1024 / 1024
        if mb >= 1024 {
            return String(format: "%.1f GB", mb / 1024)
        }
        return String(format: "%.0f MB", mb)
    }

    /// 编码友好名
    var codecText: String {
        switch codec.lowercased() {
        case "hvc1", "hev1": return "HEVC (H.265)"
        case "avc1": return "H.264"
        default: return codec.isEmpty ? "未知" : codec
        }
    }
}

// MARK: - 从 AVAsset 解析元数据

enum VideoAssetLoader {
    /// 解析本地视频 URL → VideoAsset。失败抛错。
    static func load(from url: URL) async throws -> VideoAsset {
        let asset = AVURLAsset(url: url)

        // 时长
        let duration = try await asset.load(.duration)
        let durationSec = CMTimeGetSeconds(duration)

        // 视频轨道（拿分辨率 + 编码）
        guard let track = try await asset.loadTracks(withMediaType: .video).first else {
            throw VideoError.noVideoTrack
        }
        let naturalSize = try await track.load(.naturalSize)
        let transform = try await track.load(.preferredTransform)
        // 应用 transform 得到显示方向的真实宽高
        let displaySize = naturalSize.applying(transform)
        let width = Int(abs(displaySize.width))
        let height = Int(abs(displaySize.height))

        // 编码格式
        var codec = ""
        if let formatDescriptions = try? await track.load(.formatDescriptions),
           let first = formatDescriptions.first {
            let codecType = CMFormatDescriptionGetMediaSubType(first)
            codec = fourCCToString(codecType)
        }

        // 文件体积
        let fileSize = (try? FileManager.default
            .attributesOfItem(atPath: url.path)[.size] as? Int64) ?? 0

        return VideoAsset(
            url: url,
            durationSec: durationSec,
            width: width,
            height: height,
            fileSizeBytes: fileSize ?? 0,
            codec: codec
        )
    }

    /// FourCC code（如 'hvc1'）转字符串
    private static func fourCCToString(_ code: FourCharCode) -> String {
        let bytes: [UInt8] = [
            UInt8((code >> 24) & 0xFF),
            UInt8((code >> 16) & 0xFF),
            UInt8((code >> 8) & 0xFF),
            UInt8(code & 0xFF),
        ]
        return String(bytes: bytes, encoding: .ascii)?
            .trimmingCharacters(in: .whitespaces) ?? ""
    }
}

enum VideoError: LocalizedError {
    case noVideoTrack
    case pickFailed

    var errorDescription: String? {
        switch self {
        case .noVideoTrack: return "这个文件里没有视频轨道"
        case .pickFailed: return "读取视频失败，请重试"
        }
    }
}
