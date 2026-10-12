//
//  VideoPicker.swift
//  LivePulse
//
//  PHPicker 的 SwiftUI 封装（UIViewControllerRepresentable）。
//  只选视频，单选。选完把文件拷到 app 临时目录并回调 URL。
//

import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

struct VideoPicker: UIViewControllerRepresentable {
    /// 选中视频后回调本地 URL（已拷到 app 临时目录）
    var onPicked: (URL) -> Void
    /// 选取失败回调
    var onError: (Error) -> Void

    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> PHPickerViewController {
        var config = PHPickerConfiguration()
        config.filter = .videos          // 只显示视频
        config.selectionLimit = 1        // 单选
        config.preferredAssetRepresentationMode = .current  // 不强制转码，拿原始

        let picker = PHPickerViewController(configuration: config)
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: PHPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    final class Coordinator: NSObject, PHPickerViewControllerDelegate {
        let parent: VideoPicker

        init(_ parent: VideoPicker) {
            self.parent = parent
        }

        func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
            parent.dismiss()

            guard let result = results.first else {
                return  // 用户取消，不算错误
            }

            // 视频文件类型
            let typeIdentifier = UTType.movie.identifier
            let provider = result.itemProvider
            guard provider.hasItemConformingToTypeIdentifier(typeIdentifier) else {
                parent.onError(VideoError.pickFailed)
                return
            }

            // 把视频文件拷到 app 临时目录（PHPicker 给的 URL 是临时的，回调结束就失效）
            provider.loadFileRepresentation(forTypeIdentifier: typeIdentifier) { url, error in
                if let error = error {
                    DispatchQueue.main.async { self.parent.onError(error) }
                    return
                }
                guard let srcURL = url else {
                    DispatchQueue.main.async { self.parent.onError(VideoError.pickFailed) }
                    return
                }

                // 拷贝到 app 的 tmp 目录
                let dstURL = FileManager.default.temporaryDirectory
                    .appendingPathComponent("picked_\(UUID().uuidString).mov")
                do {
                    try? FileManager.default.removeItem(at: dstURL)
                    try FileManager.default.copyItem(at: srcURL, to: dstURL)
                    DispatchQueue.main.async { self.parent.onPicked(dstURL) }
                } catch {
                    DispatchQueue.main.async { self.parent.onError(error) }
                }
            }
        }
    }
}
