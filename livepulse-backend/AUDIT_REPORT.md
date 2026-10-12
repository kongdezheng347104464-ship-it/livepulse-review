# LivePulse 脉播 直播分析系统 — 全面审计报告

> 审计日期: 2026-05-26
> 审计范围: `backend/`、`frontend/`、`docker-compose*.yml`、`nginx.conf`、`.env`、`alembic/`

---

## 一、项目整体画像

| 项目 | 内容 |
|------|------|
| **业务** | 美妆直播 OBS 录屏 → ASR + OCR + NLP 分析 → 综合评分 + 飞书通知 |
| **后端** | FastAPI 0.115 + SQLAlchemy 2.0 async + PostgreSQL + MinIO + (可选)Celery + faster-whisper + 阿里云 ASR/OCR + paddleocr + sentence-transformers |
| **前端** | Vite 5 + React 18 + AntD 5 + ECharts + Zustand + axios |
| **流水线** | 8 步：video_processing → asr → script_analysis → tactics → ocr → danmaku_analysis → correlation → scoring → insight_engine |
| **架构亮点** | `SafeResult` 容错、`StepTracker` 持久化、僵尸会话自愈、`PipelineContext` 显式类型通道、`UnicodeJSONResponse` 中文支持 |

总体水平：**单人 / 小团队产品中等偏上的成熟度**。关键路径有韧性设计（容错、自愈、可重入），但**安全、部署、数据层、测试**有明显短板。

---

## 二、🔴 高严重度问题（先修）

### 1. 真实阿里云 AccessKey 明文落盘
`backend/.env:6-7`
```
ALIYUN_ACCESS_KEY_ID=<REDACTED>
ALIYUN_ACCESS_KEY_SECRET=<REDACTED>
```
- 当前不是 git 仓库且 `.gitignore` 收录了 `.env`，**但密钥仍以明文写在用户磁盘**
- 该 AK 此刻有效，能调用 OSS / OCR / ASR 烧账单
- **必须立刻**：到阿里云 RAM 控制台禁用并轮换；新 AK 切换为最小权限子账号（仅 OSS 上传 + nls-filetrans + OCR-RecognizeBasic）

### 2. API 鉴权默认关闭
`backend/.env:15` 中 `API_KEY=` 为空，`backend/app/middleware/auth.py:36-37` 在 key 为空时直接放行：
```python
if not settings.api_key:
    return "dev-no-auth"
```
只要服务暴露到内网外，任何人都能上传 2GB 视频、删除任意 session、触发分析烧 ASR 配额。

### 3. Alembic 迁移与实际 schema 不一致
`backend/alembic/versions/7d04e4a268e9_initial_schema.py` 名字叫 `initial_schema`，但实际只 `add_column`，**根本没有 CREATE TABLE**。`backend/app/main.py:68-69` 用 `Base.metadata.create_all` 兜底建表。
- 全新数据库 `alembic upgrade head` 会失败（找不到要 alter 的表）
- 模型字段与生产 schema 漂移检测不到
- 必须重新生成基线

### 4. `upload_video` 不在事务里
`backend/app/api/upload.py:71-102` 顺序：MinIO 上传 → DB flush（无 commit） → `asyncio.create_task(run_analysis_async(session.id))`。
- 此时 session 还在 `get_db` 依赖的事务里没 commit
- pipeline 协程并发开始执行，`db.get(LiveSession, session_id)` 在 `pipeline.py:115` 用的是另一个 session_factory —— 可能查不到那一行
- DB 提交失败时孤儿对象会泄漏到 MinIO

### 5. 文件名注入风险
`backend/app/api/upload.py:94` `title=title or file.filename` 把原始文件名直接进库 500 字段，需要 sanitize。

---

## 三、🟡 中严重度问题

### 6. 后台任务用 `asyncio.create_task` 而非任务队列
`backend/app/api/upload.py:102`、`backend/app/api/pipeline.py:42`。
- 任务存活完全依赖 uvicorn 不重启
- 没有并发控制：同时上传多个视频会 OOM
- Celery 代码已写但实际未启用

### 7. `recover_zombie_sessions` 加载全表
`backend/app/services/session_recovery.py:25`：`select(LiveSession)` 取全部行，Python 端过滤。改为 `where(status.in_(LOCKED_STATES))`。

### 8. `ScriptSegment.text` 限长 5000，可能截断
`backend/app/models/script_segment.py:21` `String(5000)`。改 `Text`。

### 9. 没有任何数据库索引
所有 model 文件无 `Index(...)`。频繁查询：
- `Danmaku.session_id + timestamp_sec`
- `ScriptSegment.session_id + start_sec`
- `LiveSession.status`、`LiveSession.created_at`
- `PipelineStep.session_id + step_order`

### 10. `String(36)` UUID 主键
所有模型用 `String(36)` 存 UUID。PostgreSQL 有原生 `UUID` 类型（16 字节 vs 36 字节）。

### 11. `analysis_summary` JSONB 整存整取
列表接口把整个 `analysis_summary` 返回（含 insight_report、semantic 等大对象）。50 条 session 可能传几 MB。

### 11b. detail 接口默认 `seg_limit=100` 沉默截断（审计后发现）
`/sessions/{id}` 接受 cursor 分页但前端只调一次，1h+ 直播实际有 600-800 segments，**只返回前 100 个就当完整结果**。表现：语义时间轴后段大段空白。教训："带 cursor 的接口"≠"前端真的会分页拉"，需配合前端调用方一起审。修法：去分页改为完整返回 + 10k segments / 50k danmakus guard。

### 12. `_step_script` 跳过逻辑会丢 `ctx.segments`
`backend/app/tasks/pipeline.py:273-276`：ASR 跳过时 `ctx.segments = [placeholder]`，下游任何步骤拿 `ctx.segments` 都会拿到 placeholder。约束隐式且危险。

### 13. 前端轮询过于激进 + 状态错乱
`frontend/src/hooks/useSessionData.js:11-19`：依赖 `session?.status` 变化反复创建/销毁定时器；每次 fetch 都把整张 session 拉回来。

### 14. 进度条与真实状态脱节
`frontend/src/pages/SessionDetail.jsx:51-77` 用 300ms 定时器自动 +1~3%，再用 `maxMap` 卡上限。状态映射支持 `processing/analyzing`，但后端只写 `processing`。

### 15. CORS allow_origins 硬编码 localhost
`backend/app/main.py:101-110`。生产部署后前端将拿到 CORS 错误。

### 16. 飞书 webhook 通知没去重 / 没失败重试
`backend/app/tasks/pipeline.py:212`、`660`。同一 session 多次重跑会发多条飞书消息。

---

## 四、🟢 设计/可维护性建议

### 17. 仍保留同步 `requests==2.32.0` 依赖
`backend/requirements.txt:11`。代码已全走 `httpx`，按 `CHECKLIST.md` 这就是禁用项。

### 18. paddlepaddle 3.0 + paddleocr 2.9 但没用上
`ocr_service.py` 只 re-export 阿里云版本，**paddleocr 完全没被 import**，但 Docker 会把它（>1GB）装进镜像。

### 19. `snownlp==0.12.3` 也未必在用
情感分析若已改为 embedding，snownlp + jieba 可能是死代码。

### 20. 路由前缀和顺序约定靠注释
`backend/app/api/sessions.py:80-188` 多次写 "must be before /{session_id}"。

### 21. 配置硬编码 Windows 路径
`backend/app/config.py:36-45`、`46` 写死 `C:\Users\<user>\...\ffmpeg.exe` 和 `f:\models\faster-whisper-small`。Docker 容器无法找到。

### 22. `pool_size=10, max_overflow=20` 与 Whisper 单线程不匹配
`backend/app/database.py:6-13`。本地 ASR 是单进程长任务，DB 并发上不去。

### 23. 测试覆盖很薄
完全没有 API 层测试（upload / sessions / delete），没有数据库迁移测试。

### 24. `_session_to_dict` 没有 schema
`backend/app/api/sessions.py:16` 手写 dict 序列化。Pydantic v2 `from_attributes=True` 一行解决。

### 25. 前端 prop drilling vs zustand 混用
三层数据流（store / hook 派生 / props）让心智成本高。

---

## 五、优先级修复路线图

| 优先级 | 任务 |
|--------|------|
| **本周必修** | (1) 轮换阿里云 AK → 切子账号；(2) 强制非空 `API_KEY` 启动校验；(3) Docker 后端的 ffmpeg/模型路径修正 |
| **下周修** | (4) 重做 Alembic baseline；(5) upload commit 时序修复；(6) Danmaku/ScriptSegment 加索引；(7) `analysis_summary` 在列表接口剥离 |
| **后续优化** | (8) Celery 真正接入或加 Semaphore；(9) 前端轮询改增量；(10) 进度条用真实 step 状态；(11) 删 paddleocr/requests 死依赖；(12) API 层 e2e 测试 |
| **长期** | UUID 类型、Pydantic schemas、CORS settings 化、CI |

---

## 六、值得保留的亮点

- `PipelineContext` 用类型化字段（`ocr_raw` vs `analyzed`）防数据混入
- `SafeResult` + `step.skip()` 让非致命步骤优雅降级
- 僵尸 session 自愈在启动和删除两处都有兜底
- 阿里云 ASR 客户端写了完整的指数退避 + 抖动重试
- 前端 `ErrorBoundary`、`UnicodeJSONResponse`、`normalizeText` 表明踩过坑
