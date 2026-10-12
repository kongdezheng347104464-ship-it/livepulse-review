//
//  PlayerController.swift
//  LivePulse
//
//  封装 AVPlayer + 当前播放时间观察。时间轴拖拽、笔记打点都依赖它。
//

import Foundation
import AVFoundation
import Observation
import SwiftUI

/// 无系统控件的播放视图（AVPlayerLayer）。SwiftUI 的 VideoPlayer 强制带原生
/// 播放控件，会跟工作台自己的控制条 + 点击手势打架；这里只渲染画面，所有控制
/// 走工作台底部按钮 + 点视频手势。
struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer

    func makeUIView(context: Context) -> PlayerHostView {
        let v = PlayerHostView()
        v.playerLayer.player = player
        v.playerLayer.videoGravity = .resizeAspect
        v.backgroundColor = .black
        return v
    }

    func updateUIView(_ uiView: PlayerHostView, context: Context) {
        if uiView.playerLayer.player !== player {
            uiView.playerLayer.player = player
        }
    }
}

final class PlayerHostView: UIView {
    override class var layerClass: AnyClass { AVPlayerLayer.self }
    var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
}

@MainActor
@Observable
final class PlayerController {
    let player: AVPlayer
    /// 当前播放时间（秒），由 periodic observer 更新
    var currentTime: Double = 0
    /// 视频总时长（秒）
    var duration: Double = 0
    /// 是否正在播放
    var isPlaying: Bool = false
    /// 视频显示尺寸（用于算宽高比 / 竖横屏）。加载完成前默认竖屏占位。
    var videoSize: CGSize = CGSize(width: 9, height: 16)

    /// 宽高比（宽/高）
    var aspectRatio: CGFloat {
        videoSize.height > 0 ? videoSize.width / videoSize.height : 9.0 / 16.0
    }
    /// 是否竖屏
    var isPortrait: Bool { videoSize.height > videoSize.width }

    private var timeObserver: Any?

    init(url: URL) {
        self.player = AVPlayer(url: url)
        addPeriodicObserver()
        Task { await loadMetadata() }
    }

    // 不写 deinit 清理 observer：periodic observer 用 [weak self] 不构成循环引用，
    // player 是 controller 持有的 let 属性，随 controller 一起释放并自动拆 observer。
    // （deinit 是 nonisolated，无法安全访问 @MainActor 属性，故不在此清理）

    private func loadMetadata() async {
        guard let item = player.currentItem else { return }
        let asset = item.asset
        if let d = try? await asset.load(.duration) {
            duration = CMTimeGetSeconds(d)
        }
        // 读视频轨道显示尺寸（应用 transform 得到正确方向的宽高）
        if let track = try? await asset.loadTracks(withMediaType: .video).first,
           let natural = try? await track.load(.naturalSize),
           let transform = try? await track.load(.preferredTransform) {
            let display = natural.applying(transform)
            let w = abs(display.width), h = abs(display.height)
            if w > 0, h > 0 { videoSize = CGSize(width: w, height: h) }
        }
    }

    private func addPeriodicObserver() {
        // 每 0.2 秒回报一次当前时间
        let interval = CMTime(seconds: 0.2, preferredTimescale: 600)
        timeObserver = player.addPeriodicTimeObserver(
            forInterval: interval,
            queue: .main
        ) { [weak self] time in
            guard let self else { return }
            self.currentTime = CMTimeGetSeconds(time)
            self.isPlaying = self.player.timeControlStatus == .playing
        }
    }

    // MARK: - 控制

    func togglePlay() {
        if isPlaying {
            player.pause()
        } else {
            player.play()
        }
    }

    /// 跳到指定时间（秒）
    func seek(to seconds: Double) {
        let clamped = max(0, min(seconds, duration))
        let target = CMTime(seconds: clamped, preferredTimescale: 600)
        player.seek(to: target, toleranceBefore: .zero, toleranceAfter: .zero)
        currentTime = clamped
    }

    /// 相对跳转（如 -10 / +10 秒）
    func skip(_ delta: Double) {
        seek(to: currentTime + delta)
    }
}
