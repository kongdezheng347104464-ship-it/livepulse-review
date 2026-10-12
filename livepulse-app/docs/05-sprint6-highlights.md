# ADR · Sprint 6「高光时刻」导出设计

> **状态**：已定（owner 2026-06-10 拍板）
> **关联**：[00-decisions.md §10](00-decisions.md)（切片重定义为高光定位器）、[03-roadmap.md Sprint 6](03-roadmap.md)、[04-backend-changes.md](04-backend-changes.md)
> **影响范围**：后端（新增 2 个接口 + ffmpeg 切片）+ iOS（「高光时刻」tab + 导出）

---

## 背景

- **高光时刻 = 用户可炫耀、正向的精彩片段**（互动峰值、成功逼单瞬间），不是"改进点/问题"。
- 后端 [insight_engine/engine.py](../../WorkBuddy_full/project/backend/app/services/insight_engine/engine.py) 已产出 `timeline_highlights`，字段几乎现成：
  ```python
  { "time", "type", "reason", "start_sec", "end_sec", "score" }   # 取 top-5 insight
  ```
  但它是**按 score 排序的 top-5 insight，含正向也含负向**（`conversion` 类可能是"转化掉了"这种问题）。
- 客户端**没有原始视频文件**（视频在后端 MinIO 流式播），导出片段只能由后端生成。
- Sprint 6 目标：**展示 + 导出高光**。不做 app 内剪辑器、不接抖音 OpenSDK（见 §10）。

---

## 决策

### 1. 导出方式 = 后端切片（方案 A，否决客户端切片 B）

- 后端 ffmpeg 切片 + 返回 clip presigned URL。
- **同步接口**返回 `{ clip_url, expires_in }`，**不轮询、不建导出 job 表**。
- **用 `-c copy` 流拷贝，不重编码**（spike 实测翻转，见下）：
  ```
  ffmpeg -nostdin -ss START -i <presigned_master_url> -t DURATION -c copy -movflags +faststart out.mp4
  ```
  - `-ss` 在 `-i` 前 → 对 MinIO presigned URL 做 HTTP range seek，只拉 clip 那一段，不下整文件。
  - `-t DURATION`（= end_sec − start_sec），**不能用 `-to`**（`-ss` 在前时 `-to` 相对 seek 点会切错）。
  - 复用 video_processor.py 的 `_run_ffmpeg(cmd, timeout, desc)`（带硬超时，防 worker 卡死）。

#### 编码决策：`-c copy` 而非重编码（spike 实测翻转 2026-06-10）

> 草稿原定"重编码 `-c:v libx264` 保帧精确"。spike 推翻了它。

216MB / 61 分钟真实 master，切 90s 高光，本机 6 核：

| 方案 | 耗时 | 起点精度 | 后果 |
|---|---|---|---|
| 重编码 libx264 | **23.8s** | 帧精确 | ❌ 同步崩 → 被迫上异步队列（违反本 Sprint"不建队列"）；生产 ECS 核更少 → 60–120s；有画质损失 |
| **`-c copy`** | **0.38s** | 对齐关键帧，±4s（实测关键帧每 4s） | ✅ 同步轻松成立；零画质损失；天然保留源分辨率 |

- **±4s 起点误差恰好不重要**：按 §10，产物是"给剪映精修的原料"，主播本来就要裁；起点早几秒甚至是有用的 context lead-in。为 4s 精度换"被迫异步 + 生产 60–120s + 画质损失"不划算。
- `-c copy` 对 HEVC 源同样适用（保持原编码，Photos/剪映都吃）。
- TODO（不预建）：极少数源编码万一剪映/Photos 不认，再对那条 fallback 重编码。

### 2. 编码标准 = 不转码，原样拷贝

- `-c copy` 直接拷贝源编码（h264 或 hevc）+ 源分辨率，**不重编码、不降采样、不统一格式**。
- 与项目"不转码原画直传"核心精神一致；§2 草稿里"统一 H.264+AAC / 强制 720p"**已作废**（720p 会压坏竖屏素材，而 copy 根本不碰画质）。

### 3. 高光列表「可分享」筛选（已实测验证 2026-06-10）

- 仅透出 **`score >= 60`** 的 highlight 到「高光时刻」tab。**这是正式规则,不是临时代理**——见下方实测验证。
- **空态是常态,不是边缘情况**:实测 score>=60 下 **10/28 个 session(约 36%)高光列表为空**。iOS 端**必须**在 Sprint 6 做体面空态("这场暂无可分享高光,可能是转化/互动信号较弱"),按全局规则 9.2 降级为空、绝不伪造高光。
- **暂不加绝对地板**(owner 2026-06-10):score 是场内相对显著性,弱场的"1 条购买意向"也能过 60,绝对意义上偏弱。先上线用真实主播反馈再决定是否加"购买意向条数 ≥ N"地板。
- **tab 命名保持「高光时刻」,内容当前只有转化维度**(owner 2026-06-10):实测全部 highlight 都是 `conversion_spike`,无 `engagement_peak`(弹幕稀疏)。承诺略大于现状,等引擎补上互动来源自然扩充,先不改名。

#### 实测验证(2026-06-10,28 个真实 session)

直接查 `live_sessions.analysis_summary->insight_report->timeline_highlights`,人工扫全部 41 条:

| 验证项 | 结果 |
|---|---|
| 是否有负向高光上榜("翻车"被当高光) | ❌ **零条**。41 条全是"触发购买意向 N 条"(正向)。引擎不把 `weak_spots` 写进 timeline_highlights。**原最大担忧证伪。** |
| `score>=60` 是否都正向 | ✅ 是。该闸口对正负安全(甚至冗余,本就全正向)。 |
| score 语义 | ⚠️ **场内相对显著性**,非绝对质量。`114f5081` score 70 仅"1 条";`7981bae7` score 87 "78 条"。 |
| 空列表占比 | ⚠️ 8/28 highlights 全空;加 score>=60 后 **10/28 空**。 |
| 类型分布 | ⚠️ 41 条 **100% conversion_spike,0 条 engagement_peak**。 |

> 结论:Sprint 6 真正风险不在"翻车过滤"(已证伪)也不在工程,而在**空态常见 + 高光仅转化维度**——已通过上方三条决策消化。

### 4. 客户端约束

- 用户只能导出 `highlight_id` 指定的片段。
- **不开放** arbitrary `start_sec` / `end_sec` 给客户端——否则马上退化成"半个视频剪辑器"，超出 Sprint 6 边界。

### 5. Clip 缓存与清理

- Clip **不持久化**，按需生成 + 短时 presigned URL（`expires_in`）。
- 省掉清理 job；预生成缓存留给未来"导出量大"那一天。

### 6. 接口定义

```http
GET  /sessions/{id}/highlights
→ 200 [ { "id", "start_sec", "end_sec", "score", "type", "reason" }, ... ]
  # 后端读 insight_report.timeline_highlights，过滤 score>=60 后透出(§3)
  # 列表可能为空(约 36% 的场) → 返回 []，不是错误

POST /sessions/{id}/highlights/{highlight_id}/export
→ 200 { "clip_url": "...", "expires_in": 3600 }
  # 同步：ffmpeg -c copy 切片（HTTP-seek master）→ MinIO → 返回 presigned URL（亚秒级，§1）
```

iOS 端：
- 列表为空 → **体面空态**(§3),不是报错、不是转圈。
- 有高光：点击预览 → 「导出」→ 拿 `clip_url` → 下载 → `Save to Photos` / 系统 Share Sheet（转剪映）。

---

## 替代方案（已否决）

**B 客户端切片**（AVMutableComposition）：
- iOS 不能对远程 MP4 精确裁剪，必然退化成"先下载源视频 → 本地缓存 → 重导出"。
- 导出一个 30s 高光可能要先下 500MB，体验差。
- 引入本地视频管理 / 断点下载 / 缓存清理一整套额外复杂度，与 Sprint 6 边界冲突。

---

## 后续演进

- 导出量大 / worker 打满 → 上异步 job + 状态轮询（已有 Semaphore worker pattern，演进成本低）。
- 预生成高光 clip 缓存。
- 如确有需求再开放 arbitrary start/end → 那时才考虑 iOS AVMutableComposition。

---

## 已知 adjacent 小坑（本 ADR 不修，记录备查）

- `_build_highlights` 用 `if tr.get("start_sec")` 过滤，`start_sec == 0`（0:00 开播瞬间的高光）会被当 falsy 漏掉。清理 insight engine 时留意。

---

**Last updated**: 2026-06-10
