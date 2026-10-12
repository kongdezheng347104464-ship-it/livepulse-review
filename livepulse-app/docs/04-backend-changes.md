# Backend 配合 iOS App 的改动清单

> 后端项目根目录：`~/Desktop/WorkBuddy_full/project/backend/`
> 80% backend 不动（AI pipeline / 评分 / 笔记 / A1/A2 隔离全保留）。
> 本文档记录的是**为 iOS app 必须新增/改动的接口和逻辑**。

---

## 1. ✅ 已完成：竖屏 / 手机抖音 OCR 适配（2026-05-30 ship）

**状态**：已完成，无需重做。

**已落地的实现**（在 `backend/app/services/aliyun_ocr_service.py`）：

| 模块 | 行号 | 作用 |
|---|---|---|
| `_extract_viewer_phone_heuristic` | line 217-308 | 手机抖音 viewer count 启发式提取（基于 word bounding box） |
| `_PHONE_VIEWER_EXCLUDE_MARKERS` | line 45-48 | 排除"加热/挑战/充能/本场/点赞"等同区域误识别 |
| Fallback 路径 | line 464-475 | desktop "在线观众" regex 失败时自动 fallback 到 phone heuristic |
| `_LIKE_PATTERNS` | line 68+ | 横屏 OBS / 手机 抖音都用同一套 `<N><unit>本场点赞` regex |

**验证 fixture**：M0 调研 source `97c5f11d` 真实手机录屏，已通过测试。

**全帧 OCR 策略**意味着不再依赖固定 ROI，所以不需要 `orientation` 字段做分支处理。

---

## 2. 🔴 P0：Chunked Upload（分块上传）

**问题**：iOS 端要支持"边转码边上传"+"断点续传"+"后台上传"，必须改成分块上传协议。

### 新 endpoint

```python
POST /upload/init
  body: { filename, total_size, anchor_id, title }
  returns: { upload_id, chunk_size }

POST /upload/chunk/{upload_id}
  body: { chunk_index, chunk_data (multipart) }
  returns: { received_chunks: [0, 1, 2, ...] }

POST /upload/complete/{upload_id}
  returns: { session_id }  # 触发 pipeline

GET /upload/status/{upload_id}
  returns: { received_chunks, total_chunks }
```

### 实现细节
- 分块大小：5-10 MB（移动网络合理）
- 服务端组装：所有 chunk 收到后 concat 成完整 mp4
- 临时存储：MinIO 单独 bucket `uploads-temp`，complete 后 mv 到主 bucket
- 过期清理：未完成的 upload 24 小时后清理

### 工作量
2 天

---

## 3. 🔴 P0：APNs 推送服务

**问题**：用户关 app 后分析完成要发推送。

### 实现
- 安装 `aioapns` 库
- 配置 APNs Auth Key（`.p8` 文件，从苹果开发者后台下）
- 新增 `app/services/push/apns_service.py`：
  ```python
  async def notify(device_token, title, body, payload={}):
      ...
  ```
- pipeline 末尾加 hook：
  ```python
  # _step_insight 完成后
  await notify_analysis_complete(session)
  ```

### 推送内容模板

| 触发 | title | body |
|---|---|---|
| 早期洞察完成 | "Top3 改进点已就绪" | "你这场的 3 个最该改的点：[第一条摘要]" |
| 完整分析完成 | "复盘报告就绪" | "综合分 {score}，互动 {eng}、转化 {conv}、内容 {cont}" |
| 分析失败 | "分析失败" | "点击重试" |

### 工作量
1 天

---

## 4. 🔴 P0：Device 注册 endpoint

**问题**：iOS 端拿到 APNs device token 后要上报，跟当前 user 关联。

### 新表

```sql
CREATE TABLE user_devices (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_token VARCHAR(255) NOT NULL UNIQUE,
    platform VARCHAR(20) NOT NULL,  -- 'ios' / 'android'
    app_version VARCHAR(20),
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX ix_user_devices_user_id ON user_devices(user_id);
```

### 新 endpoint

```python
POST /devices/register
  body: { device_token, platform: 'ios', app_version: '1.0.0' }
  returns: { ok: true }

POST /devices/unregister
  body: { device_token }
  returns: { ok: true }
```

### 推送时
- 查 `user_devices WHERE user_id=X AND platform='ios'`
- 并发推送到所有 token（用户可能多设备）
- 无效 token（APNs 返回 410）自动从表清理

### 工作量
0.5 天

---

## 5. 🟡 P1：端侧 ASR 结果回传 endpoint

**问题**：iOS 端的 Apple Speech Framework 跑完后要把结果发给 backend，让 backend 跳过 ASR 步骤直接做后续分析。

### 新 endpoint

```python
POST /sessions/{session_id}/transcript
  body: {
    source: 'client_asr',  # 标识来源
    segments: [
      { start_sec: 0, end_sec: 15, text: "大家好..." },
      ...
    ]
  }
  returns: { ok: true }
```

### Pipeline 改造

```python
# _step_asr 改成：
async def _step_asr(ctx):
    # 检查是否有 client_asr 结果
    if existing := await get_client_transcript(ctx.session_id):
        await save_as_script_segments(existing)
        return  # 跳过云端 ASR
    # 否则走原流程（阿里云 ASR）
    ...
```

### 服务端 ASR 校准（M3 优化）
- iOS 端结果存为 `transcript.client_text`
- 服务端 ASR 完成后 diff，差异大的句子用服务端版本覆盖
- 差异小的保留 client 版本（节省 token）

### 工作量
1 天（不含 M3 diff 逻辑）

---

## 6. 🟡 P1：渐进式分析 status endpoint

**问题**：iOS 端工作台需要轮询当前分析进度，显示在顶栏。

### 现有 endpoint 已足够
`GET /sessions/{id}/status` 已返回 step-level 进度，复用即可。

### 改动
- 加 ETag / If-None-Match 支持，减少无变化时的网络成本
- 加 `next_milestone` 字段：`{ "type": "early_top3", "eta_seconds": 180 }`

### 工作量
0.5 天

---

## 7. 🟡 P1：高光切片识别 endpoint

**问题**：iOS 端要从服务端拿到"精准高光时刻列表"（端侧只做粗识别）。

### 新 endpoint

```python
GET /sessions/{session_id}/highlights
  returns: {
    items: [
      {
        start_sec: 320,
        end_sec: 380,
        reason: "强转化片段：3 次逼单触发 7 条强购买意图",
        score: 87,
        thumbnail_url: "..."
      },
      ...
    ]
  }
```

### 实现
- 基于现有 scoring v5 + insight engine 数据
- 找 conversion 高 / engagement 突变 / sentiment 峰值的时段
- 切片时长建议 30-90 秒（抖音切片标准）

### 工作量
3 天（含算法 + endpoint）

---

## 8. 🟢 P2：笔记同步 endpoint（已有，确认 API 形态）

iOS 端笔记本地存（SwiftData），有网时 sync 到 server。

### 现有 endpoint（确认 OK）
- `POST /sessions/{id}/notes`
- `GET /sessions/{id}/notes`
- `PATCH /notes/{note_id}`
- `DELETE /notes/{note_id}`

### iOS 端策略
- 本地有 `synced` 字段标记是否已上传
- 离线时记笔记 → `synced=false`
- 有网时批量 push 未 sync 的 → 全部成功后标 `synced=true`
- 冲突策略：客户端为准（M1 不做协同编辑）

### 工作量
0（backend 已有）

---

## 9. 🟢 P2：抖音切片导出 endpoint

**问题**：用户在 iOS app 选定切片范围后，backend 用 ffmpeg 切出 mp4 给客户端下载。

### 思考：要不要服务端切？

**方案 A：服务端切**
- iOS 端发 `POST /sessions/{id}/clip { start_sec, end_sec }` → server 用 ffmpeg 切 → 返回 url
- 优点：精准、可重切
- 缺点：增加 server 负担、用户要再下载

**方案 B：客户端切**
- iOS 用 AVMutableComposition + AVAssetExportSession 切原视频
- 优点：快、无 server 负担、视频已在本地（前提是上传前没删原视频）
- 缺点：原视频是 HEVC 时切完还要转码

**推荐方案 B**：iOS 端切（用 1080p HEVC 原视频或 720p H.264 转码版都行）

### 工作量
0（backend 不用做，iOS 端 Sprint 6 做）

---

## 改动汇总

| Priority | 改动 | 工作量 | 阻塞 iOS app |
|---|---|---|---|
| ✅ 完成 | 竖屏 OCR 适配（2026-05-30 ship） | - | - |
| 🔴 P0 | Chunked upload | 2 天 | ✅ |
| 🔴 P0 | APNs 推送 | 1 天 | ✅ |
| 🔴 P0 | Device 注册 | 0.5 天 | ✅ |
| 🟡 P1 | Client ASR 回传 | 1 天 | 不阻塞 MVP |
| 🟡 P1 | Status endpoint 优化 | 0.5 天 | 不阻塞 |
| 🟡 P1 | 高光切片识别 | 3 天 | 不阻塞 MVP |
| 🟢 P2 | 笔记 sync（已有） | 0 | - |
| 🟢 P2 | 切片导出（客户端做） | 0 | - |
| **总计 P0** | **3.5 天** | | |
| **总计 P0+P1** | **8 天** | | |

---

## 数据库迁移

新增 alembic migration：
- `0012_user_devices.py` — `user_devices` 表
- `0013_session_orientation.py` — `live_sessions.orientation` 字段

---

## 配置 / 环境变量

新增到 `backend/.env`：
```bash
# APNs
APNS_KEY_PATH=/secrets/AuthKey_XXX.p8
APNS_KEY_ID=ABC123DEF
APNS_TEAM_ID=YOURTEAMID
APNS_BUNDLE_ID=com.livepulse.app
APNS_USE_SANDBOX=false

# 上传
CHUNK_SIZE_BYTES=10485760  # 10 MB
UPLOAD_TEMP_BUCKET=uploads-temp
UPLOAD_TEMP_TTL_HOURS=24
```

---

**Last updated**: 2026-06-08
