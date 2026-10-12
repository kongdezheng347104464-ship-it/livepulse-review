# 技术栈选型

## 目标 iOS 版本
**iOS 16+**（覆盖 95%+ 用户 + 拿到 SwiftUI / SwiftData 大部分新 API）

---

## 核心栈

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | **Swift 5.9+** | 最新版 async/await 成熟 |
| UI 框架 | **SwiftUI**（不用 UIKit） | 新项目直接 SwiftUI，少写 50% 代码 |
| 并发 | **Swift Concurrency** (`async/await` / `Task`) | 比 Combine 简单，是新代码标准做法 |
| 状态管理 | **`@Observable`**（iOS 17+ 优先），fallback `ObservableObject` | SwiftUI 原生 |
| 本地持久化 | **SwiftData**（iOS 17+） | 替代 CoreData，跟 Swift 集成最好 |
| 网络 | **URLSession + async/await** | 内置够用，不用 Alamofire |
| 凭证存储 | **Keychain Services** | JWT token 持久化 |
| 包管理 | **Swift Package Manager (SPM)** | 不用 CocoaPods |
| 项目结构 | **Xcode Workspace + Module Targets** | 分层清晰 |

---

## 视频 / 媒体

| 需求 | 选型 |
|---|---|
| 相册选视频 | **PhotoKit (PHPicker)** — iOS 14+ 新版，无权限提示 |
| 视频转码（HEVC→H.264 / 1080p→720p） | **AVFoundation (AVAssetExportSession)** |
| 视频抽帧 | **AVFoundation (AVAssetImageGenerator)** |
| 视频播放 | **AVKit (AVPlayer + AVPlayerViewController)** |
| 视频剪辑（高光切片） | **AVFoundation (AVMutableComposition)** |
| 音量峰值检测（端侧亮点候选） | **AVAudioEngine + tap** |
| 端侧 ASR 中文识别 | **Speech Framework**（Apple 官方，离线，免费） |
| 富文本笔记渲染 | **`AttributedString(markdown:)`**（iOS 15+ 原生，无第三方依赖） |
| 富文本笔记存储 | **Markdown 字符串**（可移植 + 跟 backend/web 通用） |
| 视频元数据（时长/分辨率/编码） | **AVAsset.load(.tracks)** |

---

## 后台任务 / 推送

| 需求 | 选型 |
|---|---|
| 后台上传（app 关也能传完） | **URLSession.background** |
| 短时后台刷新 | **BGTaskScheduler** |
| 推送注册 + 显示 | **UserNotifications** |
| 服务端推送协议 | **APNs (Token-based auth)** — 不用老式证书 |

---

## 第三方集成

| 集成 | SDK / 方式 |
|---|---|
| 抖音分享 / 直发作品 | **DouyinOpenSDK** ✅ 官方 SDK |
| 后端 API 调用 | URLSession 直连 example.com（无需第三方 SDK） |
| 错误监控 | **Sentry iOS SDK**（跟现有 backend Sentry 同账号） |
| 推送分发 | 走自家 backend → APNs（不用第三方 push service） |

---

## 项目目录结构

```
LivePulseApp/                  # Xcode 工程根
├── App/
│   ├── LivePulseApp.swift     # @main 入口
│   └── AppDelegate.swift      # APNs 注册 / 后台唤醒
│
├── Features/                   # 按业务功能分模块
│   ├── Auth/                  # 登录注册
│   ├── Upload/                # 选视频 + 转码 + 上传
│   ├── Workspace/             # ⭐ "上传即开工"工作台（核心）
│   │   ├── VideoPlayerView.swift
│   │   ├── TimelineView.swift
│   │   ├── NotesView.swift
│   │   └── TranscriptView.swift   # 端侧 + 服务端 ASR 显示
│   ├── Report/                # 报告页（评分 / Top3 / 洞察）
│   ├── Highlight/             # 高光识别 + 切片预览 + 发抖音
│   └── Profile/               # 用户设置
│
├── Services/                   # 业务无关的工具层
│   ├── API/
│   │   ├── APIClient.swift    # URLSession 封装 + Bearer 注入
│   │   └── Endpoints/         # 各 endpoint 函数
│   ├── Auth/
│   │   ├── AuthStore.swift    # JWT 管理
│   │   └── KeychainStore.swift
│   ├── Video/
│   │   ├── VideoCompressor.swift   # AVAssetExportSession 封装
│   │   ├── ThumbnailGenerator.swift
│   │   └── HighlightDetector.swift # 端侧粗识别
│   ├── ASR/
│   │   └── LocalSpeechRecognizer.swift  # Speech Framework 封装
│   ├── Upload/
│   │   ├── BackgroundUploader.swift     # URLSession.background
│   │   └── ChunkedUploader.swift        # 分块上传
│   ├── Push/
│   │   ├── PushRegistrar.swift          # APNs 注册
│   │   └── NotificationHandler.swift    # 点通知跳转
│   └── Douyin/
│       └── DouyinShareService.swift     # OpenSDK 封装
│
├── Models/                     # 数据模型（对应 backend schema）
│   ├── Session.swift
│   ├── Note.swift
│   ├── Anchor.swift
│   └── User.swift
│
├── DesignSystem/               # 设计系统
│   ├── Colors.swift
│   ├── Typography.swift
│   └── Components/             # 复用组件
│
├── Resources/
│   ├── Assets.xcassets         # 图片 / 颜色
│   ├── Localizable.strings     # 中文文案
│   └── Info.plist
│
└── Tests/
    ├── UnitTests/
    └── UITests/
```

---

## 关键库版本（M2 启动时锁定）

| 库 | 最低版本 | 用途 |
|---|---|---|
| Swift | 5.9+ | 编译 |
| iOS Deployment Target | 16.0 | 系统 API 基线 |
| Xcode | 15.0+ | 工具链 |
| DouyinOpenSDK | 待官方文档 | 抖音分享 |
| Sentry-Cocoa | 8.x+ | 错误监控 |

---

## 不用的技术（决策记录）

- ❌ **UIKit**：SwiftUI 已经成熟，新项目直接上
- ❌ **CoreData**：SwiftData 替代
- ❌ **Combine**：Swift Concurrency 替代（除非用第三方库强依赖）
- ❌ **Alamofire / Moya**：URLSession 内置够用
- ❌ **CocoaPods**：SPM 替代
- ❌ **RxSwift**：Combine / async/await 替代
- ❌ **Whisper.cpp on-device**（M2 不用，M3 候选）—— 跟服务端 ASR 角色重叠，详见 [00-decisions.md §6](00-decisions.md)
- ❌ **MarkdownUI / RichTextKit**（M2 不用）—— iOS 15+ `AttributedString(markdown:)` 已够用

---

**Last updated**: 2026-06-08
