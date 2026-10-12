# LivePulse iOS App

> 主战场是 iOS app — 用户在手机上完成「直播 → 录屏 → 上传 → 复盘 → 切片 → 发抖音」全链路。
>
> 工程内部代号 WorkBuddy / livepulse-app，对外品牌 **LivePulse / 脉播**。

---

## 一句话定位

**直播复盘笔记 · 主打功能性**

- ❌ 不打数据精度（抖音 API 比我准）
- ❌ 不打功能广度（蝉妈妈/爱复盘比我多）
- ✅ 打**主播工作流闭环**：上传即开工 + AI 复盘建议 + 一键切片直发抖音
- ✅ 打**手机端原生体验**：单设备完成所有动作，不需要切到 PC

---

## 核心交互原则（产品口号）

### 🌟 "上传即开工"

用户在 app 内点击上传 → **立刻进入复盘工作台**，可以播放视频、记笔记、看时间轴，
**而不是看着进度条干等 5 分钟**。

AI 分析在后台跑，结果**渐进式出现**：
- 端侧 ASR 边跑边显示话术（"正在识别 30%..."）
- 评分/Top3 改进点等服务端跑完后推送进来
- 用户可以**关闭 app，分析完成时收通知**直达报告

详见 [docs/02-ux-flow.md](docs/02-ux-flow.md)。

---

## 项目文档索引

| 文档 | 内容 |
|---|---|
| [docs/00-decisions.md](docs/00-decisions.md) | 战略决策 + 转向记录（为什么选 Swift / 为什么放弃 web 主战场 / 为什么"边用边等"） |
| [docs/01-tech-stack.md](docs/01-tech-stack.md) | 技术栈选型（Swift / SwiftUI / AVFoundation / Speech / APNs / 抖音 OpenSDK） |
| [docs/02-ux-flow.md](docs/02-ux-flow.md) | 完整用户流程（含"边用边等"实现） |
| [docs/03-roadmap.md](docs/03-roadmap.md) | 路线 + 任务清单 + 阻塞依赖 + 工作量估算 |
| [docs/04-backend-changes.md](docs/04-backend-changes.md) | 后端要配合改的（chunked upload / APNs / device 注册 / 竖屏 OCR 适配） |

---

## 项目结构

```
livepulse-app/
├── README.md                  # 本文件（项目总览 + 索引）
├── docs/                      # 所有设计文档
│   ├── 00-decisions.md
│   ├── 01-tech-stack.md
│   ├── 02-ux-flow.md
│   ├── 03-roadmap.md
│   └── 04-backend-changes.md
└── (Xcode 项目将来放这里)
```

---

## 相关链接

- 后端 / Web 前端项目根目录：`~/Desktop/WorkBuddy_full/project/`
- 备案中域名：`example.com`
- 阿里云 ECS：`<your-server-ip>`（升配中）

---

**Last updated**: 2026-06-08
