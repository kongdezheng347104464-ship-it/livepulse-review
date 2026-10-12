# QA 报告：上传 → 处理 → 分析 → 结果展示 全链路

**测试日期**：2026-05-29
**测试视频**：阿野美妆直播 1h15min 录屏（`2026-05-29 15-11-09.mp4`，duration 4517s, audio 137.9MB）
**测试主播**：阿野（已有 anchor "阿野"，复用）
**测试场次标题**：教化妆的阿野
**测试者**：owner（作者）+ Claude（QA + UX 视角）
**系统版本**：commit `ac3ff6c`（scoring v5 / Conversion tab / 人群质量卡）
**测试 session id**：`ad9f759c-15fb-4ecc-94e8-b8071fffcc6a`

---

## 一、测试方法与说明

| 维度 | 做法 |
|---|---|
| 测试入口 | 真实用户路径：浏览器 (Chrome) → http://127.0.0.1:5173/ → 上传页 |
| 上传方式 | 手动选择文件（拖拽测试一并执行） |
| 监控 | pipeline.log 实时 tail + DB 直查 + 前端 DevTools (Network/Console) |
| 信源等级 | ✅ 实测（用户截图 + 后端日志双向印证） / 🤔 推断（无直接现象支撑） |
| **未覆盖** | 移动端、慢网络、并发上传、跨浏览器、生产部署、多 anchor 切换 |

**关键时间线（用户感受层面）**

| 时刻 | 事件 |
|---|---|
| `18:08:37` | 用户点击"上传并分析" |
| `18:09:04` | 上传完成（27s）+ session 入库（id ad9f759c）+ 自动跳 `/sessions/{id}` |
| `18:09:09` | Pipeline 启动 T4（视频处理） |
| `18:09:04 → 18:31:50` | **用户看到 "正在分析中 0% 准备中" 整整 22 分钟，0% 不动** |
| `18:12:53` | T4 完成（duration=4517s, 151 frames） |
| `18:16:59` | T6 OCR 开始（同时 Semantic tactic NameError 静默） |
| `18:24:28` | T6 OCR 完成（1182 events: speech 581 / unknown 561 / gift 32 / system 8） |
| `18:24:28 → 18:31:49` | 7 min 21 s 完全静默（实际在跑 T6b viewer-count OCR） |
| `18:31:49` | T8 Live-event analysis 启动 |
| `18:31:49` | T9 Scoring：**danmaku_total=0**（应有 581+） |
| `18:31:50` | Pipeline 完成 status=analyzed composite=37.0 |
| `18:31:50` | 列表页卡片**自动**切换到"已完成"（用户未刷新 ✅） |

**总耗时 22 分 41 秒，全程用户屏幕 "0% / 准备中" 不变。用户直接反馈："我以为系统死了，想关 tab。"**

---

## 二、问题清单（按严重等级）

> 严重度定义：
> - **Critical** — 用户会以为系统崩了 / 会流失；数据丢失或核心能力静默缺失
> - **High** — 严重影响体验或诱导误操作，但不至于让用户立刻关 tab
> - **Medium** — 体验粗糙有解法，不阻塞主流程
> - **Low** — cosmetic / dev-only / 长期债

---

### 🔴 Critical

#### **P-1 / P-6**：Pipeline 进度永远 0%，用户在 22 分钟焦虑期想关 tab

**现象**：上传任何视频 → 自动跳 `/sessions/{id}` → 永远显示 "正在分析中... 0% / 准备中"，**直到 status 跳成 analyzed 才整页切换**

**用户原话**（直接引用）：
> "我以为系统死了，想关掉 tab"
> "我肯定会以为这个 AI 系统出问题了啊"

**复现**：100% 复现，每一次上传都触发

**根因**：`app/services/step_tracker.py:120` `await self.db.flush()` 仅 flush 不 commit；pipeline 跑全程在同一未提交事务里，`/status` endpoint 用另一个 db session 查 `pipeline_steps` 表，因事务隔离永远拿不到中途状态 → `steps=[]` → `progress=0`

**实测证据**：
```
GET /sessions/ad9f759c.../status   ←  84 次轮询全部返回 progress=0 steps=[]
后端 pipeline.log：T4→T5→T7→T6→T8→T9 都跑过且日志清晰
DB pipeline_steps 表：0 条 (跑完 22 分钟后才一次性 commit)
```

**修复路径**：
1. **立即**（5 分钟改完）：`step_tracker._upsert` 把 `flush()` 改成 `commit()`。每个 step 转换状态后立即对 `/status` 可见
2. **配套**（10 分钟）：前端轮询展示文案随 step_name 变化：`准备中 → 视频处理中 → 语音转录 → 话术分析 → 弹幕识别 → 评分中 → 完成`
3. **额外**：阶段提示动画（呼吸点 / 圆环 / 进度条颜色），避免"在 same step 时仍像卡死"

**修复后预期**：首屏从"22 分钟 0% 不动"变成"每 1-7 分钟有可见的阶段切换 + 进度条 0→14→28→...→100"

---

#### **B-3**：Semantic tactic analysis 抛 NameError 被静默吞掉

**日志证据**：
```
2026-05-29 18:16:59 WARNING [app.tasks.pipeline]
  Semantic tactic analysis failed (non-fatal): name 'nn' is not defined
```

**影响**：embedding_tactics 整步**没有任何输出**，所有 segment 的 `analysis_tags["embedding_tactics"]` 全部缺失。前端分析页 "话术策略分布" 仅靠 keyword fallback（有限规则）显示数据 — 但**核心 embedding-based 战术识别能力等于关闭**。

**用户视角**：分析报告会缺一整块洞察，**用户完全不知道**

**修复路径**：
1. **立即**：`_logger.warning("...: %s", e)` 改成 `_logger.exception("...:")` 把完整 traceback 打出来定位 nn 来源
2. **追因**：可能在 sentence_transformers / torch 内部初始化时回退路径触发；与 B-6 (`f:/models`) 强相关
3. **长期**：embedding step 改为"必须配置成功，否则 startup 报错"，不要静默 fallback

---

#### **B-6**：Windows 路径残留 `f:/models`

**位置**：`app/services/embedding/embedding_service.py:118`
```python
cls._model = SentenceTransformer(model_name, cache_folder="f:/models")
```

**影响**：Mac 上 `f:/models` 是无效路径，SentenceTransformer cold-start 时会回退到 HuggingFace 重新下载模型。如果网络受限（**已知 `fonts.gstatic.com` 都连不通**），下载失败 → 模型 = None → 后续访问触发 NameError → **极可能就是 B-3 nn 报错的间接源**

**修复**（一行）：
```python
from pathlib import Path
cache_folder = str(Path.home() / ".cache" / "workbuddy_models")
cls._model = SentenceTransformer(model_name, cache_folder=cache_folder)
```

---

#### **B-8**：Danmaku 持久化丢失 — 581 speech events 全部消失

**实测证据**：
```
[ocr][ad9f759c] interactions=1182 source_type_dist=
  {'unknown': 561, 'gift': 32, 'speech': 581, 'system': 8}
[danmaku_analysis][ad9f759c] speech=581 non_speech=601    ← step 拿到了
[scoring][ad9f759c] step_start ... danmaku_total=0          ← scoring 看不到
DB:                            danmakus=0                    ← 表里 0 条
```

**异常静默**：pipeline.log 应该出现 `[normalize][ad9f759c] raw=621 valid=... `, `[danmaku_analysis] dropped X unknown`, `[danmaku_analysis] done` 等日志，**全部缺失**。但也没有 `Semantic intent analysis failed` 或 `danmaku_analysis skip` 错误日志 — exception 似乎在 `_step_danmaku` 内部 try/except 兜底外被吞

**影响链**：
- danmakus 表 0 条 → unique_speakers=0 → engaged_rate=0% → 人群质量卡显示"参与率 0.0%"
- conversion windows=0 → "最容易触发购买的话术" #1/#2/#3 卡片**全部消失**
- 转化漏斗各阶段=0 → 漏斗显示"互动 0 / 犹豫 0 / 比价 0 / 购买考虑 0 / 强购买 0"
- 但 OCR 抓到的 581 条 speech 全是真实的主播话术，**真实信号被完全丢弃**

**修复路径**：
1. **立即**：`_step_danmaku` 的 try/except 块里改成 `_logger.exception("danmaku_analysis fatal:")` + 把 step status 标 `failed` 而非 `skip`，并 raise 让 invariant harness 抓到
2. **断点 debug**：在 line 759 `for r in events_to_persist: ctx.db.add(Danmaku(...))` 前后加 `_logger.info("about to add %d danmakus", len(events_to_persist))` 重跑一场就知道是 add 没跑还是 commit 没成功
3. **invariant 补漏**：加一条 invariant —— OCR 有 speech events 时 DB danmakus 不能为 0，捕获以后所有同类回归

---

#### **R-6**：假洞察 — "弹幕 0 条 持续互动高峰" 矛盾

**现象**：
- "最强互动片段 #1 1:00-2:30 持续互动高峰 (90s, **弹幕 0 条**)"
- "关键洞察 #1 1:00-2:30 持续互动高峰 (90s, **弹幕 0 条**)"
- 描述："1:00-2:30 为持续 90s 的互动高峰，累计弹幕 **0** 条，互动 9 次，密度为全场平均的 **0.0x**"

**问题**：0 条弹幕 + 0.0x 密度 + "全场平均"也是 0 → 一切是 0 → insight engine 仍硬吐"互动高峰"。这是**虚假的"洞察"**

**根因 1**：insight engine 在 danmaku 数据缺失时没有 short-circuit
**根因 2 (上游)**：B-8 数据丢失让 insight engine 拿到了空输入但仍跑了规则

**修复路径**：
1. insight_generator 在 `total_danmaku == 0` 时，整个 engagement 类 insight **全部 suppress**，不要硬吐
2. 配合 data_completeness banner 升级，明确说"互动 / 转化 insight 已隐藏"

---

### High

#### **U-2**：上传页没有任何边界提示
无格式 / 大小 / 时长上限。1h15min mp4 / mov / mkv 哪个能传？多大会被拒？
**修复**：上传按钮上方一行 hint：`支持 MP4/MOV，单文件 ≤ 5GB，最长 3 小时`（实际值看后端配置）

#### **U-6**：直播标题 placeholder "自动使用文件名" 但实际没自动填
**复现**：选完文件后标题框仍空
**修复**：选完文件**立即往 input value 写文件名**（无后缀），或改 placeholder 文案为 "留空将使用文件名"

#### **U-9**：上传前零前端 validation
**修复**：HTML5 `<input accept="video/mp4,video/quicktime">` + JS 读 `file.size` 上限 + `<video>` 预读 duration

#### **U-10**：新建主播重名 → 上传整体失败
**用户原话**：
> "如果是已有主播（已有阿野），在新建主播栏里重新填'阿野'，上传会失败"
**修复**：检测重名时 inline "已存在该主播，将复用"+自动切到选择 tab；不要走 409

#### **U-11 / L-7**：场次计数与实际状态不一致
- 主播下拉合计 21（7+4+2+2+2+2+2）vs 列表副标 "14/15 场分析完成"
- 上传后副标变 "15 场分析完成"，但 1 场还在 processing
**修复**：副标改成 "已分析 N 场 / 处理中 M 场"；主播下拉 "X 场（含 Y 场分析中）"

#### **R-1**：已分析 session 进入仍先显示 "0% 准备中" 3 秒
**根因**：前端 store 初始 state 默认 processing，从 cache → fetch → 切换
**修复**：组件挂载时若 sessionMeta.status 已知是 analyzed，直接渲染 Skeleton 而非 "0% / 准备中"

#### **R-3**：数据完整性 banner 文案误导 root cause
**现象**：写 "本场**未采集到**弹幕数据"，但实际 OCR 抓到了 581 条，是 B-8 持久化丢了
**修复**：文案改成 "弹幕数据缺失（可能是 OCR 无识别 / 持久化失败 / 视频中无弹幕区）" + 后端把缺失类型也算出来回写 data_completeness.reason

#### **R-4**：漏斗右下角神秘 "总弹幕 1"
**实测**：DB `danmakus=0`，OCR speech=581，scoring `danmaku_total=0`。这个 "1" 哪里来的？
**待定位**：前端 `ConversionFunnel` 组件里 `totalDanmaku` 值的来源

#### **R-5**：漏斗 -100% 假比较
**现象**：互动/犹豫/比价/购买考虑/强购买 全 0，右侧显示 0% -100% -100% -100% -100%
**根因**：baseline 只 1 场无效（需要 ≥ 3），但前端仍然渲染 -100% 落差
**修复**：baseline 不够时各阶段右侧用 "—" 或 "基线不足" 占位，不渲染 -100%

#### **R-7**：顶部 "可信度 64% · 可参考" 来源不透明
**问题**：danmakus=0 / unique_speakers=0 的场子仍判 "64% 可信"，缺少解释
**修复**：可信度旁加 ⓘ tooltip 列出计算因子（ASR / OCR / embedding / step_success），鼠标悬停展开

#### **R-10**：关键洞察 50 / 44 / 42 数字没标签
**用户原话**：
> "我不明白是什么意思，不明白这是什么数据"
**修复**：数字旁加一行小字 "影响力 50 / 100"，或者直接写 "score 50"。无标签数字 = 用户认知断层

#### **B-2**：pipeline step 编号 T 错乱
日志里 step 真实顺序是 `T4 → T5 → T7 → T6 → T6b → T8 → T9 → T10`，T 编号无对应实际 STEP_ORDER index
**修复**：log message 用 step_name 而非 T 前缀

---

### Medium

#### **U-1**：拖拽上传完全无响应
**修复**：标准 HTML5 drag-drop 监听器，把整个 form 作为 dropzone

#### **L-3**：Google Fonts 慢加载导致 font flash
Console intervention 显示 Slow network → fallback font. 内网环境 / 弱网下首屏会出现字体切换闪烁
**修复**：把 Inter 字体打包到 `frontend/public/fonts/`，CSS `font-display: swap`

#### **L-4**：多个 session 都叫"测试"，无主播信息辅助区分
**修复**：卡片左上加主播 chip / 头像

#### **P-4**：processing 期间页面无识别信息
**修复**：loader 上方放 session 卡片摘要（标题 / 主播 / 上传时间 / 视频时长）

#### **B-4**：OCR unknown 比例 47%
151 帧 → 1182 events 中 561 unknown（47%）。已知问题，待 OCR 噪音规则优化

#### **R-8 / R-9**：empty state 把布局变形 / 漏斗下方空白
**用户原话**：
> "页面布局不严谨，漏斗下方为空白没有内容"
**根因**：当某些数据缺失（如 tacticDistribution=[]），整个 panel 被 `&& length>0` 隐藏，导致两侧高度不对称、留大片空白
**修复**：每个 panel 写明确 empty state（"本场未抓到话术策略 — 检查 ASR" 等占位卡）

#### **R-11**：50 / 44 / 42 数字与标签横向间距过大
**修复**：CSS flex 收紧 gap，或把数字挪到标签同一行右侧

#### **R-12**：关键洞察展开后字号太小
**修复**：原因/结果/置信度行用 13px 而非 11px，加行间距

---

### Low

| ID | 现象 | 修复 |
|---|---|---|
| **L-1** | favicon 404 | 放一个 16x16 .ico 到 frontend `public/` |
| **L-2** | React Router v6→v7 future flag 警告 | `<BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>` |
| **L-5** | 综合分格式不统一（37 vs 42.1 vs 21.2） | 统一 `.toFixed(1)` 或全整数 |
| **L-6** | 垃圾桶 hover 出现，confirm 防误删未验证 | 测试一次；若无 Popconfirm 立即加上 |
| **P-3** | antd `[antd: message] Static function...` 警告 | 根组件包 `<App>` |
| **P-5** | "分析中"金色标签无动画 | 加一个呼吸 / 旋转 SVG |
| **B-1** | pipeline T 编号顺序 T4/T5/T7/T6/T6b/T8 命名错位 | 同 B-2 修复 |
| **B-5** | T6 OCR 单步 7.5 min | 阿里云 OCR 并行调用（当前同步 151 frames） |
| **B-7** | pipeline 中段 OCR→viewer_count 之间 7+ 分钟无日志 | 每个 step start/end 都打 INFO log |
| **L-10** | 列表卡片自动从"分析中"切"已完成" ✅ | 正面 finding |
| **WS-1 / WS-2** | Workspace v5 指标 + 视频 / 时间线 / 笔记 / AI 分析 整体可用 ✅ | 正面 finding |
| **WS-3** | "00:19 来。" ASR 切片粒度过小 | 短句合并到 ≥ 5s 段 |

---

## 三、UX 综合评价（真实用户视角）

| 维度 | 评价 |
|---|---|
| 整体流程是否"安心" | **❌ 不安心**。22 分钟 0% 不动是流失最大威胁 |
| 用户是否随时知道系统正在干什么 | **❌ 知道得太晚**。所有阶段提示只在日志里，UI 永远是"准备中" |
| 是否存在等待焦虑 | **❌ 严重**。用户实际反应"想关 tab" |
| 是否有不必要步骤 | ✅ 步骤合理 — 上传 / 主播 / 标题 三件套 |
| 是否存在认知断层 | **❌ 多处**：神秘"总弹幕 1"、神秘"可信度 64%"、神秘"50/44/42"、神秘"-100%" |
| 是否容易误以为系统崩了 | **❌ 实测如此**。22 分钟 0% 不动 |
| 是否像专业 AI 产品 | **部分**。Workspace 完成度高（v5 指标 / 视频 / 时间线 / 笔记 / AI 分析），但**前置流程毁了第一印象** |

---

## 四、修复优先级建议

### P0 — 必须在下一次给真实用户看前修（4-8 小时工作量）

| 修复 | 涉及 bug | 工作量 | 影响 |
|---|---|---|---|
| step_tracker `flush → commit` | P-1 / P-6 | 5 分钟改 + 测 | 22 分钟焦虑消除 |
| 前端轮询拼阶段文案 + 进度条 | P-1 配套 | 1-2 小时 | 用户看到"OCR 中"/"评分中" |
| 修 B-6 Windows 路径 + 重新 cache 模型 | B-3 / B-6 | 30 分钟 + 模型下载时间 | embedding 真生效，话术战术识别恢复 |
| 修 B-8 danmaku 丢失（先加完整 log，二跑定位） | B-8 / R-3 / R-4 / R-5 / R-6 | 1-3 小时 | 整套观众/转化/互动数据恢复 |
| 加 invariant：OCR speech > 0 时 DB danmakus 必 > 0 | B-8 防回归 | 30 分钟 | 永久保险 |

### P1 — 第一批用户试用前必做（4-6 小时）

| 修复 | bug |
|---|---|
| Insight engine 在 danmaku=0 时 suppress 假洞察 | R-6 |
| 数据完整性 banner root cause 准确化 | R-3 |
| 已分析 session 进入不再显示 "0%" | R-1 |
| 关键洞察数字加标签 / 字号 / 间距修复 | R-10 / R-11 / R-12 |
| empty state 占位卡 | R-8 / R-9 |
| 上传页边界提示 + 前端 validation + 重名 inline | U-2 / U-6 / U-9 / U-10 |

### P2 — 体验加分（按余力）

文案 / favicon / antd warning / React Router flag / 字体内嵌 / 漏斗 -100% 隐藏 / 拖拽支持 / hover 删除 confirm

### P3 — 长期债

OCR 并行 / unknown 比例下降 / ASR 短句合并 / 历史 14 场重跑

---

## 五、最终结论

**当前是否达到生产可用水平？**

❌ **不达到**。即便 B-8 数据丢失只在这场出现（待定位），P-1 进度 0% 的体验对**任何真实用户**都是流失级 UX。

**结构性问题（不只是 cosmetic）**

1. **事务隔离把进度信息困在 pipeline 内部**（P-1 根因）—— 设计层缺陷
2. **关键失败被静默吞掉**（B-3 nn / B-8 danmaku 丢失）—— observability 不足
3. **"看似有数据但其实没数据" 的虚假洞察**（R-6）—— 数据缺失时降级策略缺失
4. **Windows 残留迁移漏网**（B-6）—— 平台迁移 checklist 没覆盖到三方 lib 配置

**正面 finding（系统在哪些地方是真好用的）**

1. ✅ 上传 → 自动跳转 / 列表自动刷新（无需 F5）
2. ✅ v5 人群质量卡 / 浏览人数曲线 / 综合分体系：数据正确时展示力强
3. ✅ Workspace 视频 + 时间线 + 笔记 + AI 复盘整套：完成度高
4. ✅ 数据完整性 banner：意识到要警告用户（虽然文案不准）
5. ✅ Invariant harness 35/35 全绿（说明已有保险范围内的代码质量高）

**最关键的一句话**

> **修 step_tracker 那一行 flush→commit，让用户能看到 "正在 OCR 识别 (62/151 帧)" 而不是 "0% 准备中" — 这一条修复带来的体验提升，比所有其他 bug 加起来都大。**

---

## 附录：测试用 session 数据（ad9f759c）

```
status: analyzed
duration_sec: 4517 (1h 15min 17s)
composite_score: 37.0
sub_scores: content=38.1 engagement=30.2 conversion=42.5

script_segments: 828
danmakus: 0 (BUG B-8, OCR 抓到 581 speech)
viewer_snapshots: 150 (29.85s/snapshot, 正常)

normalized_metrics:
  unique_speakers: 0 (受 B-8 影响)
  avg_viewer_count: 239.41
  engaged_audience_rate: 0.0 (受 B-8 影响)
  strong_purchase_count: 0
  purchase_intent_per_viewer: 0.0

OCR source_type 分布:
  speech:   581 (49.2%)
  unknown:  561 (47.5%)
  gift:      32  (2.7%)
  system:     8  (0.7%)
  total:   1182 events

pipeline 各步耗时:
  T4 视频处理       3 min 44 s
  T5 ASR            2 min
  T7 脚本分析       2 min
  T6 OCR           7 min 29 s    (单步最长)
  T6b viewer OCR   7 min 21 s    (静默 7 min)
  T8 danmaku       < 1 s         (有 581 speech 但持久化丢)
  T9 scoring       < 1 s
  T10 insight      < 1 s
  合计              22 min 41 s
```

---

**报告生成**：Claude (QA + UX 视角)
**协作流程**：用户实时浏览器操作 + 屏幕截图 + 后端日志/DB 双向印证
**未来 retrospective 复盘**：建议达成 P0 修复后开 `docs/retrospective.md` 复盘"为什么 B-3 / B-8 这类静默失败一直藏到 QA 才暴露"
