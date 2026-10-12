# 路线图 + 任务清单

> 活文档：完成的勾选，新增的加在对应阶段。

---

## 阶段划分

| 阶段 | 时间窗 | 核心目标 |
|---|---|---|
| **M1 收尾** | 2 周内 | A3 上线 + 竖屏 OCR 适配 |
| **M2 启动准备** | M1 收尾期间并行 | 苹果/抖音开发者账号、Swift 学习 |
| **M2 App MVP** | A3 上线后 4-6 周 | iOS app MVP（5 个核心页面）发 TestFlight |
| **M2 App 公测** | MVP 后 2-4 周 | App Store 上架 + 邀请 10 个真实主播试用 |
| **M3** | 待 M2 数据决定 | 高光切片直发 / Android / 团队版 |

---

## 阻塞依赖关系

```
[ICP 备案]
   └─→ [A3 上线] ────────────────┐
                                   ↓
[竖屏 OCR 适配] ←─[找真实主播录屏] │
   └────────────────────────────→ [iOS app 联调]
                                   ↑
[Backend chunked upload]            │
[Backend APNs 推送]                 │
[Backend device 注册]               │
                                   ↑
[苹果开发者账号] ──────────────────┘
[抖音开放平台账号] ────→ [高光切片直发]
```

---

## M1 收尾任务

### 🔴 必做

- [x] ✅ **竖屏 / 手机抖音 OCR 适配**（2026-05-30 已 ship，见 [04-backend-changes.md §1](04-backend-changes.md)）
- [ ] **A3 上线**（等 ICP 备案下来）
  - ECS 升配 2c2g → 4c16g
  - secrets 全部重生成
  - HTTPS + nginx 配好
- [ ] **当前所有 web 未 commit 改动** —— 分组 commit 别再攒

### 🟡 选做

- [ ] 找 2-3 段真实抖音手机直播录屏（OCR 已能跑，但 iOS app 联调时用更安心）
- [ ] frontend_v2 完善（C2-C3 教练文案 owner 拍板后做）
- [ ] 邀请 3-5 个内测主播给 web 版反馈

---

## M2 启动准备（跟 M1 并行）

### 🔴 必做（异步等审核）

- [x] ✅ **注册 Apple ID + 免费开发者账户**（已完成）
- [ ] **付费升级 Apple Developer Program**（$99/年）—— **Sprint 5 启动前 1 周** 付款激活（推送 + TestFlight 必须）
- [ ] **注册抖音开放平台开发者**（1-2 周审核）
  - 创建 LivePulse 应用
  - 申请「分享视频到抖音」权限
  - 🔍 待 web 验证：是否需要企业资质
- [ ] **学 Swift / SwiftUI**（边学边做）
  - 推荐课程：100 Days of SwiftUI (Paul Hudson)
  - 目标：2 周内能写 Hello World + 基础 SwiftUI 页面
- [ ] **下载 Xcode 15+ + 准备 Mac 开发环境**
  - Apple Silicon Mac 优先（模拟器更快）
  - 当前 Mac Intel 也能跑，模拟器慢一点

### 🟡 选做

- [ ] 看 1-2 个开源的 Swift 视频处理项目（学 AVFoundation 实战）
- [ ] 注册 Sentry 项目用于 iOS app 错误监控

---

## M2 App MVP（核心 4-6 周）

按 sprint 拆分，每个 sprint 1 周。

> **实际进度（2026-06-09 核对 git）**：Sprint 1 ✅ / Sprint 2 选视频部分✅(转码挪到 S3) /
> Sprint 4 工作台+笔记 ✅(端侧 ASR/亮点 = 4C/4D 未做) / **额外做了主播浏览 + 点场次流式进工作台 + 上传选建主播**。
> 已 6 个 commit（见 git log）。下一个大块 = **Sprint 3 上传**（前置 backend chunked upload）。

### Sprint 1（第 1 周）：项目骨架 + 登录 ✅ 已完成

- [x] Xcode 项目创建（Swift + SwiftUI + iOS 17）
- [x] 项目结构搭建（features/services/models 分层）
- [x] 设计系统基础（Theme：紫色品牌色 + 圆角 + 间距）
- [x] Login / Register 页面
- [x] APIClient（URLSession + async/await + Bearer + 条件解包 envelope + 401 拦截）
- [x] AuthStore + Keychain 存 JWT
- [x] 跑通登录 → 拿 token → 调 `/auth/me`（模拟器真机登录 admin 成功）

### Sprint 2（第 2 周）：相册选视频 ✅（转码挪到 Sprint 3）

- [x] PHPicker 集成（零权限选视频）
- [x] AVAsset 解析（时长、分辨率、编码、竖横屏、体积）
- [x] AVPlayer 视频预览
- [ ] ~~转码~~ → 挪到 Sprint 3（端侧预处理一起做）
- [ ] ~~端侧抽帧~~ → 用到时再做

**额外完成（不在原 sprint 计划）**：
- [x] 主播列表 + 场次分析表（移植 web 浏览能力，拉 backend /anchors/ /sessions/）
- [x] 点场次 → 流式播后端视频 + 记笔记（presigned URL）
- [x] 上传表单：主播输入即搜索（选已有/建新）+ 直播标题

### Sprint 3（第 3 周）：上传 + 后台模式

- [x] ✅ **Chunked upload backend**（/upload/init /chunk /complete /status，已 ship + 测试）
- [x] ✅ **iOS 分块上传链路**（ChunkedUploader：init→逐块PUT→complete + 进度条）
- [x] ✅ 上传进度 UI；完成 → 自动进后端场次工作台
- [x] ✅ 验证：iOS 上传 60s 竖屏 → 后端跑完整 pipeline → analyzed 综合分 30.7
- [❌] ~~端侧转码压缩~~ → **2026-06-10 owner 拍板不做**（见下）
- [ ] URLSession.background 后台上传存活（前台已通；背景存活是单独精细活）
- [ ] 上传中断恢复（断点续传，backend status endpoint 已支持）

#### 🔴 转码决策：不转码，原画直传（owner 2026-06-10 拍板）

- **原因**：OCR 读弹幕/在线人数小字是核心价值。手机录屏本就竖屏 888×1920（不算高清），
  压到 720p 会缩到 ~333×720 → OCR 小字大概率废。
- **实证**：iOS 上传原画 60s 竖屏，后端 OCR 正常出综合分 30.7 → 原画对 OCR 友好。
- **取舍**：放弃"压缩省上传时间"，换"OCR 质量 + 实现简单 + 零质量风险"。
- **副作用**：上传体积大（100MB-1GB），建议 WiFi 上传；4G 大文件慢 → 靠「上传即开工」体验掩盖。
- **端侧 ASR**（省后端 ASR 时间）独立于转码，仍是 M3 候选，不受此决策影响。

**Sprint 3 已达成核心目标**：选视频 → 真上传到后端 → 后端分析出分，闭环跑通。

### Sprint 4（第 4 周）：⭐ Workspace 工作台（核心）✅ 4A+4B 完成

- [x] 视频播放器（AVPlayer + 自定义控件 + 播放器联动缩小）
- [x] 时间轴组件（拖拽 playhead + 笔记标记点）
- [x] Tab 切换：笔记 / 话术 / 洞察 / 切片（后三个占位）
- [x] 笔记 CRUD（本地 SwiftData：Note + 时间锚 + ⭐收藏 + 删除确认 + Markdown）
- [x] ✅ **笔记后端同步**（2026-06-10：内容+时间推后端备份，重装/跨设备恢复，收藏/标签留本地）
- [ ] 4C：端侧 ASR（Speech Framework）渐进式 ← 未做（已分析场次直接拉后端转写，端侧 ASR 是边传边显示优化）
- [ ] 4D：端侧亮点候选（音量峰值） ← 未做

### Sprint 5（报告/分析展示）✅ 大部分完成（2026-06-10）

> 没做"独立报告页 + 推送"，而是把分析内容做进工作台的 tab（更符合复盘工作流）。

- [x] ✅ **洞察 tab**：AI 报告（综合分 + 三分 + insight_report 洞察 + 点跳转视频）
- [x] ✅ **话术 tab**：后端转写字幕跟读（高亮 + 滚动跟随 + 点跳转）
- [x] ✅ **AI 选段点评**（差异化武器）：选话术段 → range-analysis → 结果 + 历史记录
- [x] ✅ **跨场进步趋势图**（B4 差异化）：Swift Charts 4 线 + 进步提示
- [x] ✅ 场次分析表（综合分配色 + 三分标签）
- [ ] APNs 推送（device 注册 + 通知）← 未做（需付费 Apple Developer + backend APNs）

### Sprint 6（高光定位器，不做剪辑器）— 2026-06-10 重定义

> ⚠️ 原"切片 + 抖音发布"砍掉（剪映降维碾压，详见 [00-decisions.md §10](00-decisions.md)）。
> 改为：AI 定位高光 → 导出给剪映精修。**不再卡抖音 OpenSDK。**
> 📐 **工程/接口设计已定**：[05-sprint6-highlights.md](05-sprint6-highlights.md)（后端切片 + 同步返回 + 可分享筛选）。
> ✅ **2026-06-10 ship**：6A 后端（GET /highlights + POST export，291 测试绿）+ 6B iOS（高光 tab + 空态 + 导出 Share Sheet，编译通过）。

- [x] ✅ "切片" tab 改名"高光"：列 AI 高光候选（时段 + 原因 + 评分）+ 点跳转预览
- [x] ✅ 导出该时段视频片段（后端 `-c copy` 切片 → presigned URL → iOS 下载 → 系统分享 sheet）
  - 🔍 决策：选后端切（A 方案 + `-c copy`，spike 实测 0.38s）；客户端切（B）已弃，见 [05-sprint6-highlights.md](05-sprint6-highlights.md)
- [ ] 真机/模拟器端到端 runtime 验证（编译已过，UI 流程待跑）
- [❌] ~~app 内剪辑器 / 抖音 OpenSDK / 一键发抖音~~ → 砍掉（剪映替我们做）

**定位**：我们是"教练 + 高光定位"，不是"又一个剪辑工具"。

---

## 📊 实际进度速览（2026-06-10 收口）

**已完成**（12 commit，均真机/真数据验证）：
- 登录注册 / 主播列表 / 场次表
- 上传：backend chunked upload + iOS 分块上传 + 断点续传 + 每块重试 + 后台宽限
- 工作台：播放器 + 时间轴 + 4 tab 框架
- 笔记：时间锚 + 收藏 + Markdown + **后端同步备份**
- 话术：字幕跟读 + **AI 选段点评 + 历史**
- 洞察：AI 报告 + 点跳转
- **跨场进步趋势图**

**两大差异化武器全落地**：AI 选段点评（vs 爱复盘独家）+ 跨场进步追踪（B4）。

**剩余**（不阻塞核心）：切片(卡 OpenSDK) / 端侧 ASR/亮点 / APNs 推送(需付费账号) / TestFlight 内测。

---

## M2 App 公测（MVP 后 2-4 周）

- [ ] TestFlight 内测（10-20 人）
- [ ] 跑通 1-2 个真实主播的完整复盘流程
- [ ] 收集崩溃 / 反馈
- [ ] App Store Connect 元数据准备（截图 / 描述 / 关键词 / 隐私政策）
- [ ] 提交 App Store 审核
- [ ] 上架

---

## M3 候选（待 M2 数据决定）

| 功能 | 触发条件 |
|---|---|
| 团队版（MCN 老板看团队数据） | 单个主播留存 > 30% 后再考虑 B 端 |
| Android 版（Kotlin / Compose） | iOS DAU > 100 后 |
| 多语言（粤语 / 英语） | 海外主播咨询 |
| AI 助手聊天（基于历史复盘） | RAG 准备好后 |
| **Whisper.cpp on-device ASR** | 用户反馈 Apple Speech 准确率不够 + 服务端等待难忍 + 新机型性能跟上 |
| **笔记 @ 引用 AI 洞察** | 笔记功能日活够高，用户问"能不能直接引用 AI 那句话" |
| **笔记导出 PDF / 分享卡片** | 主播开始有"晒复盘"需求 |

---

## 工作量总估算

| 阶段 | 估算 | 备注 |
|---|---|---|
| M1 收尾 | 2 周 | 主要等备案 |
| Swift 学习 | 1-2 月（并行） | 边学边做 Sprint 1-2 |
| M2 App MVP | 6 周 | 每 sprint 1 周 |
| M2 公测 + 上架 | 2-4 周 | 含 TestFlight + 苹果审核 |
| **总计** | **3-4 个月** | M2 App 上线 |

---

## 风险登记表

| 风险 | 概率 | 严重度 | 缓解 |
|---|---|---|---|
| Swift 学习曲线超预期 | 中 | 高 | 备选 React Native 兜底（owner 已拒绝） |
| 抖音 OpenSDK 审核拒 | 低 | 高 | 提前 1-2 周提审 |
| 苹果 App Store 拒上架 | 低 | 高 | 4.2 套壳风险已规避（有 native 功能） |
| 端侧 ASR 中文专业词识别差 | 中 | 中 | 服务端 ASR 兜底 + 智能 diff |
| 后台上传被 iOS 杀进程 | 中 | 中 | URLSession.background 配 `discretionary=false` |
| 大文件上传成本（流量费） | 低 | 中 | 端侧降码率后 200-300MB/场 |

---

**Last updated**: 2026-06-08
