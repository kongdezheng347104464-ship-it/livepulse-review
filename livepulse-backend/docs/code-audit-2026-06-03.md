# 代码审计报告 · 2026-06-03

**范围**：前后端全栈
**维度**：上线就绪（production readiness）+ 代码质量/技术债
**不含**：安全审计（A1/A2 已有 12+ 跨租户 invariant 守护；如需独立深审走 `/security-review`）
**审计人**：Claude（自动扫描 + 人工研判）
**代码状态**：大量改动未 commit（A6 / B4 like_count / deploy/prod / 测试修复）

---

## 总评

整体**健康度良好**，尤其测试纪律（~312 个后端测试，invariant 体系完善）和注释质量（TECH_DEBT / 决策理由都有文档化）在个人项目里属于上乘。本次发现 **0 个致命阻断**，但有 **2 个上线前必修的资源/配置问题**，以及若干技术债。

| 维度 | 🔴 P0 必修 | 🟡 P1 上线前 | 🟢 P2 可延后 |
|------|-----------|-------------|-------------|
| 上线就绪 | 1（临时文件泄漏） | 2（ffmpeg 超时 / prod frozen 配置） | 2 |
| 代码质量 | 0 | 2（死代码 / 无前端测试） | 3 |

---

## 一、上线就绪

### 🔴 P0-1 临时文件泄漏 — 会撑爆 ECS 磁盘

**位置**：`backend/app/tasks/pipeline.py` `_async_pipeline` 的 `finally` 块（line 313-339）

**问题**：每次分析创建两类临时文件，pipeline 结束时**只清理了下载的源视频**（`local_video.unlink()`），漏清：
1. `output_dir`（`mkdtemp("livestream_")`，line 266）—— 装全部抽帧。1h28m 视频 = 178 帧，约 **50-200MB**
2. `.playback.mp4`（line 1327）—— H.264 重编码产物，1h28m 实测 **139MB**，上传 MinIO 后未删本地

**影响**：ECS 系统盘仅 40GB。一场 1h 视频泄漏 ~300MB，**跑十几场 /tmp 就满 → 整个系统挂**。dev 机磁盘大没暴露，生产必炸。

**修法**：`finally` 块加
```python
import shutil
if output_dir.exists():
    shutil.rmtree(output_dir, ignore_errors=True)
playback = local_video.with_suffix(".playback.mp4")
if playback.exists():
    playback.unlink(missing_ok=True)
```
（`/_debug/*` 的 rerun endpoint 已经正确 `rmtree` 了，主 pipeline 反而漏 —— 对齐即可）

**风险**：低。验证：跑一场后 `ls /tmp/livestream_* /tmp/*.playback.mp4` 应为空。

---

### 🟡 P1-1 ffmpeg subprocess 无 timeout — 卡死冻结 worker

**位置**：`backend/app/services/video_processor.py` 4 处 `subprocess.run(...)`（line 169/209/220/263），均 `check=True` 无 `timeout=`

**问题**：ffmpeg 遇到损坏帧 / 异常容器可能**永久 hang**。playback_encode 是最大耗时步骤（占视频时长 14%），一旦某帧卡住，该 celery worker 被永久占用，并发池 -1，最终全堵。

**修法**：每个 `subprocess.run` 加 `timeout=`（按操作给值，如 probe 30s、transcode `max(600, video_sec*0.5)`），捕获 `subprocess.TimeoutExpired` → 标记 step failed 而非冻结。

**风险**：中（要选合理 timeout，太小会误杀长视频正常编码）。

---

### 🟡 P1-2 生产配置漏 `ALIYUN_FROZEN=false` — 部署即瘫

**位置**：`deploy/prod/.env.production.example`（本会话新建）

**问题**：`config.py` 里 `aliyun_frozen: bool = True`（欠费时代的硬刹车默认值）。生产 env 模板**没覆盖这个值**，照模板部署 → 所有 OCR/ASR 调用直接 `RuntimeError("阿里云 API 已冻结")` → pipeline 全失败。

**修法**：`.env.production.example` 加
```
# 解冻阿里云 OCR/ASR（config 默认 True 是欠费时代的保险，生产必须显式 false）
ALIYUN_FROZEN=false
```
**本次审计已直接修复**（见文末）。

**附带建议**：`aliyun_frozen` 默认值本身是个反模式 —— "默认禁用核心功能"违反最小惊讶。建议 M2 改默认 `False` + 欠费时用监控告警而非硬编码默认值。

---

### 🟢 P2-1 Minio client 重复实例化（10 处 / 6 文件）

`Minio(...)` 在 main / pipeline / sessions / upload / workspace / debug 各自 new。无共享工厂、无连接池。**影响小**（minio-py 内部有 urllib3 pool），但属重复代码 + 每请求握手开销。建议抽 `app/services/storage.py` `get_minio_client()` 单点（lru_cache）。

### 🟢 P2-2 docker-compose 废弃 `version:` 字段

`docker-compose.yml` + `docker-compose.standalone.yml` 有 `version: "3.8"`，Compose v2 已忽略并告警。删掉即可（我新写的 `deploy/prod/docker-compose.prod.yml` 已不含）。

---

## 二、代码质量 / 技术债

### 🟡 Q1 死代码

| 文件 | 状态 | 处理 |
|------|------|------|
| `frontend/src/components/PipelineProgress.jsx`（3.8KB） | 0 处 import，已被 ProgressOverlay 取代 | 建议删 |
| `backend/app/api/pipeline.py` 4 个 import | `HTTPException`/`select`/`PipelineStep`/`LiveSession` 文件精简后遗留，均仅 import 行用到 | 建议删 |
| `InsightPanel.jsx` | git working tree 已 deleted | 提交时确认 |

### 🟡 Q2 前端零测试

后端 ~312 测试（invariant + 单元 + 集成）覆盖扎实；**前端 0 测试**。M1 可接受，但 `insightAdapter.js`（375 行数据转换）、`textDecoder.js`（uXXXX 解码）、`LikeChart` 的 `pickUnit`/`cummax` 这类纯函数**值得补单测**（Vitest，低成本高回报）。列 M2。

### 🟢 Q3 `pipeline.py` 1664 行 god file

编排器 + 11 个 step 函数 + 评分公式 + celery 配置 + retry 全挤一个文件。能跑但难测难维护。重构方向：step 函数拆到 `app/tasks/steps/`，评分拆 `scoring.py`。**M1 不动**（无明确收益，重构有风险），M2 加新 step 时顺手拆。

### 🟢 Q4 单元测试盲区

`pipeline.py` step 函数 / `aliyun_ocr_service.py` / `video_processor.py` 只有集成测试覆盖，无隔离单测。其中 OCR 文本解析（`_extract_like_count` 等）已补单测✅，其余靠真实 pipeline 跑验证。可接受，列 M2 候选。

### 🟢 Q5 异常处理抽查 — 健康

`except Exception` 共 ~50 处，抽查均**有日志 + 有降级语义**（非静默吞）。`safe_ocr_wrapper` / pipeline step 的 skip 机制是有意的"非致命降级"，符合"数据缺失降级为空"守则。TODO/FIXME 仅 1-2 个真实项。print() 仅在 `__main__` CLI 块，非调试残留。**这块无需动作**。

---

## 三、立即修复项（本次审计已做）

1. ✅ `deploy/prod/.env.production.example` 补 `ALIYUN_FROZEN=false`（P1-2，trivial + 安全，属修我自己挖的坑）

其余均**仅记录不擅动**，等 owner 排期决策。

---

## 四、建议处理顺序

| 优先级 | 项 | 工时 | 状态 |
|--------|----|----|---------|
| 🔴 立刻 | P0-1 临时文件清理 | 20 min | ✅ **已修**（pipeline.py finally 块 rmtree output_dir + unlink playback） |
| 🟡 上线前 | P1-1 ffmpeg timeout | 1 h | ✅ **已修**（_run_ffmpeg 包装 + 两类超时 + 单帧 skip + probe 前移；5 单测） |
| 🟡 上线前 | P1-2 prod frozen 配置 | — | ✅ **已修**（.env 模板 + compose backend/worker 透传 ALIYUN_FROZEN） |
| 🟢 顺手 | Q1 删死代码 | 15 min | ✅ **已修**（删 PipelineProgress.jsx + api/pipeline.py 4 死 import） |
| 🟢 顺手 | P2-2 删 docker-compose `version:` | 5 min | ✅ **已修**（两个 compose 文件 + config 校验通过） |
| 🟢 M2 | P2-1 Minio 工厂 / Q2 前端测试 / Q3 拆 pipeline / Q4 补单测 | — | M2 |

**本会话已修 P0-1 / P1-1 / P1-2 / Q1 / P2-2（5 项），仅剩 M2 批次（无上线阻断）。**

---

## 附：审计方法

- 文件规模扫描（pipeline.py 1664 行最大，TranscriptPanel.jsx 617 行前端最大）
- TODO/FIXME/HACK/TECH_DEBT 全量 grep
- 异常处理模式抽查（裸 except / 静默 pass）
- 外部调用 timeout 覆盖（subprocess / minio / http）
- 资源生命周期（mkdtemp vs rmtree 配对 / 前端 setInterval vs clear 配对）
- 死代码（组件 import 引用计数 / import 使用计数）
- 测试覆盖广度（按文件 test_ 计数）
- 生产配置一致性（config 默认值 vs prod env 模板）
