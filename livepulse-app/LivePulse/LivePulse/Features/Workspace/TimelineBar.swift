//
//  TimelineBar.swift
//  LivePulse
//
//  自定义时间轴：进度条 + 可拖拽 playhead + 笔记标记点。
//  点/拖时间轴跳转视频；笔记标记点显示在对应时间位置。
//

import SwiftUI

struct TimelineBar: View {
    let duration: Double
    let currentTime: Double
    /// 笔记时间点（秒）数组，渲染成标记点
    let noteMarkers: [Double]
    /// 拖拽/点击 seek 回调
    let onSeek: (Double) -> Void

    @State private var isDragging = false
    @State private var dragTime: Double = 0

    private let barHeight: CGFloat = 6
    private let knobSize: CGFloat = 16

    var body: some View {
        VStack(spacing: 6) {
            GeometryReader { geo in
                let width = geo.size.width
                let progress = duration > 0 ? (displayTime / duration) : 0
                let knobX = width * progress

                ZStack(alignment: .leading) {
                    // 背景轨
                    Capsule()
                        .fill(LPColor.border)
                        .frame(height: barHeight)

                    // 已播放部分
                    Capsule()
                        .fill(LPColor.accent)
                        .frame(width: max(0, knobX), height: barHeight)

                    // 笔记标记点
                    ForEach(noteMarkers.indices, id: \.self) { i in
                        let m = noteMarkers[i]
                        let mx = duration > 0 ? width * (m / duration) : 0
                        Circle()
                            .fill(LPColor.scoreMid)
                            .frame(width: 8, height: 8)
                            .offset(x: mx - 4)
                    }

                    // 拖拽手柄
                    Circle()
                        .fill(.white)
                        .frame(width: knobSize, height: knobSize)
                        .shadow(color: .black.opacity(0.2), radius: 2, y: 1)
                        .overlay(Circle().stroke(LPColor.accent, lineWidth: 2))
                        .offset(x: knobX - knobSize / 2)
                }
                .frame(height: knobSize)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            isDragging = true
                            let ratio = max(0, min(1, value.location.x / width))
                            dragTime = ratio * duration
                        }
                        .onEnded { value in
                            let ratio = max(0, min(1, value.location.x / width))
                            let t = ratio * duration
                            onSeek(t)
                            isDragging = false
                        }
                )
            }
            .frame(height: knobSize)

            // 时间文字
            HStack {
                Text(timeText(displayTime))
                Spacer()
                Text(timeText(duration))
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(LPColor.textMute)
        }
    }

    private var displayTime: Double {
        isDragging ? dragTime : currentTime
    }

    private func timeText(_ sec: Double) -> String {
        let total = Int(sec.rounded())
        let h = total / 3600
        let m = (total % 3600) / 60
        let s = total % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, s) }
        return String(format: "%d:%02d", m, s)
    }
}
