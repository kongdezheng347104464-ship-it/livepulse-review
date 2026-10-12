# ADR-0002：重计算必须对"上一次的好结果"非破坏

**状态**：已接受（架构约束），M1 廉价版可选，M2 完整版必做
**日期**：2026-06-03
**决策者**：owner + claude（一次"数据反复缺失"根因复盘后产出）

---

## SYSTEM INVARIANT #1 — 非破坏性重计算

> 任何重计算（analysis / viewer / OCR / 任意 rerun）必须满足：
> 1. **不得破坏已有的成功结果**
> 2. 新结果必须先写 **staging**
> 3. 只有成功才 **commit / 原子替换**
> 4. **失败永远不能降低系统已知的最好状态（last known good）**
>
> 落到 schema：session 持有一个指向"已发布结果"的指针
> （`last_successful_analysis_id`），重跑产生新 result，**成功才换指针**。
> 僵尸恢复（startup）只清"未发布的 run"，**绝不降级已发布的 result**。
>
> 这是本系统的状态机地基。M2 统一执行模型
> （Run=immutable / Result=append-only / Publish=atomic pointer switch）
> 是它的完整实现；在此之前由"测试场次隔离纪律"守住缺口
> （见 [[feedback-test-session-isolation]]），**不做逐域局部补丁**。

---

## 背景

2026-06-03 一次会话中，"分析数据缺失"反复出现多次。根因复盘发现绝大多数
**不是数据真丢了**，而是两类问题：

1. **可感知性（observability）**：浏览器旧 tab / 服务重启 → UI 状态失效，刷新即恢复。
   数据可靠性无损。
2. **可靠性（reliability）**：**失败的重计算摧毁了已有的好结果** —— 这才是真问题。

具体两个事故都属于第 2 类：
- 重触发已 analyzed 的场次 → 跑挂 → status 从 `analyzed` 刷成 `processing`→`error`
  （僵尸恢复），**已有的好报告被隐藏**（41e9e83d / b456b873，数据其实完整）。
- `rerun-viewer-count` 是 DELETE-then-insert → 对被 720p 覆盖的视频重抽得 0 条 →
  **把原有 viewer 快照删光**（935ca358）。

用户视角："昨天有报告，今天点了重分析，报告没了" → 理解成"AI 把我数据搞丢了"，
**即使数据库一条没丢，信任感已经崩了**。这比"用户不知道要刷新"严重得多。

---

## 决策

**任何重计算（re-analysis / rerun / re-derive）必须对上一次成功结果非破坏：**

> 先算到 staging，成功才原子替换；失败则保留上一次的好结果，仅标记"最近一次重跑失败"。
> **绝不"先删后跑"或"开跑就清旧状态"。**

这条同时约束：
- pipeline 重分析（`_async_pipeline`）
- `rerun-viewer-count` / `rerun-scoring` / `rerun-ocr-onwards` 等所有 debug rerun
- 未来任何 re-derive 路径

### 反模式（现状，要改）

```
analyzed + 好报告
  → 重分析开始：status='processing'（清掉 analyzed 态）
  → 失败：status='error'
  → 结果：好报告被隐藏 / viewer 快照被删
```

### 正确模式

```
analyzed + 好报告（last good）
  → 重分析开始：后台跑，主状态/旧结果不动
  → 成功：原子替换为新结果
  → 失败：保留 last good，仅置 last_run_failed 标记
UI：仍显示上一次成功结果 + "⚠️ 最近一次重分析失败"
```

---

## 实施

### M1 廉价版（可选，改 orchestrator 一处，无迁移）

`pipeline._async_pipeline` 开头判断：**该 session 是否已有成功分析**
（`analysis_summary IS NOT NULL AND status='analyzed'`）。
- 无（首次）→ 现有逻辑（processing/error 正常）
- 有（重分析）→ 不动主 status / 不清 summary；新结果只在全部 step 成功后原子写入；
  失败只记 error_message + 一个 `last_run_failed` 信号，status 留 `analyzed`。

同理 `rerun-viewer-count`：抽到 0 条时**不删旧快照**（`if not records: return 保留`）。

### M2 完整版

引入运行/结果分离：
- `last_successful_analysis_*`（或独立 analyses 表 + `analysis_version`）
- session 主 status 稳定为 `analyzed`，重跑状态走独立 `latest_run`
- UI 明确区分"当前显示的结果版本"与"最近重跑状态"

---

## 配套纪律：测试场次隔离（同次复盘产出）

事故的触发是 **拿真实演示场次做测试**（A6/rerun 验证跑在 41e9e83d/b456b873/935ca358 上）。
- **纪律**：演示/真实场次（演示·/手机直播/小鱼 等）**禁止** rerun/re-trigger/destroy；
  所有破坏性验证只在专用 throwaway 测试场次（标题含"测试"/"test"）上做。
- **持久强制**（M2）：加 `protected`/`is_demo` 标志位 + rerun/trigger endpoint guard
  拒绝受保护场次。文档化的纪律是最弱形式 —— flag + 代码 guard 才根治。
- 已写入 memory，供后续 session 遵守。

---

## 相关

- `app/tasks/pipeline.py` `_async_pipeline`（status 翻转点）
- `app/api/debug.py` rerun-* endpoints（DELETE-then-insert 模式）
- 僵尸恢复机制（startup 把卡 processing 的刷 error）—— 与本约束交互，需一并考虑
- boot_id 自动刷新提示（解决根因 A 可感知性，独立项，P2）
