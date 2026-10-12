//
//  ChunkedUploader.swift
//  LivePulse
//
//  分块上传客户端：init → 逐块 PUT → complete，对接 backend /upload/* endpoints。
//  这一版上传原文件（不转码、前台上传）；转码 + 后台上传是后续精细化步骤。
//

import Foundation
import Observation
import UIKit

// MARK: - API DTO

private struct ChunkedInitRequest: Encodable {
    let filename: String
    let totalSize: Int
    let anchorId: String?
    let newAnchorName: String?
    let title: String

    enum CodingKeys: String, CodingKey {
        case filename, title
        case totalSize = "total_size"
        case anchorId = "anchor_id"
        case newAnchorName = "new_anchor_name"
    }
}

private struct ChunkedInitResponse: Decodable {
    let uploadId: String
    let chunkSize: Int
    let totalChunks: Int

    enum CodingKeys: String, CodingKey {
        case uploadId = "upload_id"
        case chunkSize = "chunk_size"
        case totalChunks = "total_chunks"
    }
}

private struct ChunkedStatusResponse: Decodable {
    let receivedChunks: [Int]
    let totalChunks: Int
    enum CodingKeys: String, CodingKey {
        case receivedChunks = "received_chunks"
        case totalChunks = "total_chunks"
    }
}

struct ChunkedCompleteResponse: Decodable {
    let sessionId: String
    let status: String
    let anchorName: String?
    let title: String?

    enum CodingKeys: String, CodingKey {
        case status
        case sessionId = "session_id"
        case anchorName = "anchor_name"
        case title
    }
}

// MARK: - Uploader

@MainActor
@Observable
final class ChunkedUploader {
    enum Phase: Equatable {
        case idle
        case uploading
        case finalizing
        case done
        case failed
    }

    var phase: Phase = .idle
    /// 总进度 0...1
    var progress: Double = 0
    var errorMessage: String?
    var result: ChunkedCompleteResponse?

    private let session = URLSession(configuration: .default)

    // 断点续传状态：失败重试时复用同一个 upload，只补缺失的块
    private var currentUploadId: String?
    private var currentChunkSize = 0
    private var currentTotalChunks = 0

    /// 上传一个本地视频文件到 backend，触发后端分析。
    /// 失败后再次调用 = 断点续传（复用 upload_id，跳过已收的块）。
    func upload(
        fileURL: URL,
        filename: String,
        anchorId: String?,
        newAnchorName: String?,
        title: String
    ) async {
        phase = .uploading
        errorMessage = nil
        result = nil

        // 后台宽限：切走 app 时争取一点继续上传的时间（非完整后台存活）
        let bgTask = UIApplication.shared.beginBackgroundTask(withName: "lp-upload")
        defer {
            if bgTask != .invalid { UIApplication.shared.endBackgroundTask(bgTask) }
        }

        do {
            let totalSize = fileSize(of: fileURL)
            guard totalSize > 0 else { throw UploadError.emptyFile }

            // 1. init（仅首次；重试时复用上次的 upload_id）
            let uploadId: String
            if let existing = currentUploadId {
                uploadId = existing
            } else {
                let initRes: ChunkedInitResponse = try await APIClient.shared.post(
                    "/upload/init",
                    body: ChunkedInitRequest(
                        filename: filename, totalSize: totalSize,
                        anchorId: anchorId, newAnchorName: newAnchorName, title: title
                    )
                )
                uploadId = initRes.uploadId
                currentUploadId = initRes.uploadId
                currentChunkSize = initRes.chunkSize
                currentTotalChunks = initRes.totalChunks
            }

            // 2. 查已收的块（断点续传：跳过已传的）
            let status: ChunkedStatusResponse = try await APIClient.shared.get(
                "/upload/status/\(uploadId)"
            )
            let received = Set(status.receivedChunks)
            progress = 0.95 * Double(received.count) / Double(currentTotalChunks)

            // 3. 逐块 PUT（只传缺失的，每块自动重试）
            let handle = try FileHandle(forReadingFrom: fileURL)
            defer { try? handle.close() }

            var done = received.count
            for index in 0..<currentTotalChunks where !received.contains(index) {
                try handle.seek(toOffset: UInt64(index * currentChunkSize))
                let data = try handle.read(upToCount: currentChunkSize) ?? Data()
                try await putChunkWithRetry(uploadId: uploadId, index: index, data: data)
                done += 1
                progress = 0.95 * Double(done) / Double(currentTotalChunks)
            }

            // 4. complete
            phase = .finalizing
            let completed: ChunkedCompleteResponse = try await APIClient.shared.post(
                "/upload/complete/\(uploadId)"
            )
            progress = 1
            result = completed
            phase = .done
            currentUploadId = nil   // 成功 → 下次是全新上传
        } catch {
            errorMessage = (error as? APIError)?.errorDescription
                ?? (error as? UploadError)?.errorDescription
                ?? error.localizedDescription
            phase = .failed
            // 保留 currentUploadId → 用户重试时断点续传
        }
    }

    /// 单块上传 + 自动重试（3 次指数退避）
    private func putChunkWithRetry(uploadId: String, index: Int, data: Data) async throws {
        var lastError: Error?
        for attempt in 0..<3 {
            do {
                try await putChunk(uploadId: uploadId, index: index, data: data)
                return
            } catch {
                lastError = error
                if attempt < 2 {
                    try? await Task.sleep(nanoseconds: UInt64(500_000_000 * (attempt + 1)))
                }
            }
        }
        throw lastError ?? UploadError.chunkFailed(index: index, status: -1)
    }

    // MARK: - 内部

    private func putChunk(uploadId: String, index: Int, data: Data) async throws {
        let url = APIConfig.baseURL.appendingPathComponent("/upload/chunk/\(uploadId)")
        let boundary = "lp-\(UUID().uuidString)"

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 120  // 单块上传给足时间
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        if let token = KeychainStore.loadToken() {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        var body = Data()
        body.appendString("--\(boundary)\r\n")
        body.appendString("Content-Disposition: form-data; name=\"chunk_index\"\r\n\r\n")
        body.appendString("\(index)\r\n")
        body.appendString("--\(boundary)\r\n")
        body.appendString("Content-Disposition: form-data; name=\"file\"; filename=\"chunk\"\r\n")
        body.appendString("Content-Type: application/octet-stream\r\n\r\n")
        body.append(data)
        body.appendString("\r\n")
        body.appendString("--\(boundary)--\r\n")

        let (_, response) = try await session.upload(for: req, from: body)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            let code = (response as? HTTPURLResponse)?.statusCode ?? -1
            throw UploadError.chunkFailed(index: index, status: code)
        }
    }

    private func fileSize(of url: URL) -> Int {
        (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0 ?? 0
    }
}

enum UploadError: LocalizedError {
    case emptyFile
    case chunkFailed(index: Int, status: Int)

    var errorDescription: String? {
        switch self {
        case .emptyFile: return "视频文件为空"
        case .chunkFailed(let i, let s): return "第 \(i + 1) 块上传失败（\(s)）"
        }
    }
}

private extension Data {
    mutating func appendString(_ s: String) {
        if let d = s.data(using: .utf8) { append(d) }
    }
}
