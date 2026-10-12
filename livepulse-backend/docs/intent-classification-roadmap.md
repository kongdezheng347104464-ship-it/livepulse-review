# 弹幕意图分类 · 演进路线

**性质**：活文档（按全局规则"长讨论用活文档"），决策路径 + 当前状态。
**初版**：2026-05-30 晚，阿野 ad9f759c 抽样后产出。
**当前状态**：已搁置，不在 M1 优先级。新原型词库已扩，未回滚。

---

## 1. 背景速览

**触发**：用户问"BAAI/bge-small-zh-v1.5 能否换 Youtu-Embedding"，连带产出对当前意图分类质量的诊断。

**当前实现**：
- `backend/app/services/intent/embedding_intent_analyzer.py` 用 bge-small + Top-K weighted voting 对 5 类原型分类（interaction/hesitation/comparison/purchase_consideration/strong_purchase）
- 原型词库 `backend/app/services/script_analyzer/config/intent_prototypes.json`
- 2026-05-30 已扩词：strong_purchase 15→44，purchase_consideration 12→22（commit 未独立 ship，跟一起进 demo 准备）

**诊断结果**（ad9f759c 阿野 75min · 226 条 strong_purchase 人审 n=20）：
- True positive: 1 条（"我要这个刷子"）
- 模糊: 2 条（"怎么定" / "2号吧"）
- False positive: 17 条
- **Precision ≈ 5%**

---

## 2. 已讨论话题

- [Youtu-Embedding 评估](#21-youtu-embedding-评估)
- [Precision 5% 的真因诊断](#22-precision-5-的真因诊断)
- [5 层 Gate 架构](#23-5-层-gate-架构方案-a)
- [LLM-as-Classifier 方案](#24-llm-as-classifier方案-b)
- [成本扩展担忧](#25-成本扩展担忧)

### 2.1 Youtu-Embedding 评估

**结论：不可行**（在 Mac Intel CPU + 当前使用场景下）。

- 规格：2B 参数 / 2048 维 / 8K 序列 / CMTEB 77.46 (#1 2025-09)
- 体积推测 4-8GB，CPU 推理慢 30-100 倍
- 维度 384→2048 全部缓存失效 + scoring v5 系数需 retune
- 升级路径如必要，优先 `bge-base-zh-v1.5`（768 维 / 100MB），不上 Youtu

### 2.2 Precision 5% 的真因诊断

不是分类器不准，是**输入数据不成立**。

| 真因 | 估占比 |
|---|---|
| OCR 噪音（"az tk" / "e" / @用户乱码）| ~40% |
| OCR 把主播话/字幕/教程混进弹幕 | ~25% |
| Source_type 闸口缺失（ad9f759c 历史数据 source_type=None）| ~15% |
| Prototype 过度泛化（新加的"买它/拿下/我要了"语义太开放）| ~15% |
| Confidence threshold 缺失（argmax 强行选档，无 UNKNOWN bucket）| ~5% |

**关键观察**：新原型词库扩充把 strong_purchase 从 0% 推到 36.4%，但 precision 仅 5%，**比"全 0"更具误导性**。

### 2.3 5 层 Gate 架构（方案 A）

用户提出的修当前架构方案：

```
OCR
 → Layer 1: Source Gate（speech-only）
 → Layer 2: Text Quality Gate（长度/中文占比/负面关键词）
 → Layer 3: Confidence Threshold + UNKNOWN bucket
 → Layer 4: 双重命中（embedding + keyword AND）  ← 被否决
 → Layer 5: 漏斗可信度 UI
```

**评价**：架构方向正确，但 Layer 4 反对——退回 keyword 系统，与 embedding 化的初衷冲突，召回率会暴跌。替代方案：strong_purchase 单独提高 embedding 阈值（0.45→0.65）。

### 2.4 LLM-as-Classifier（方案 B）

**架构上更优**：用 Qwen Max 直接 batch 分类，跳过 embedding + prototype + rules 全套。

- 预期 precision: 70-90%（LLM 对中文上下文比 embedding 相似度强一个量级）
- 不维护原型词库 / negative keywords / 阈值 / source gate 后半段
- 不为新品类（护肤/食品/服饰）扩词
- 顺手能拿到"分类原因"做 UI tooltip

### 2.5 成本扩展担忧

**用户担心**：用户多了 LLM 成本会不会爆炸。

测算（qwen-max 保守价）：

| 用户数 | 月成本（qwen-max）| 月成本（qwen-plus，1/4 价）|
|---|---|---|
| 10 内测 | ¥600 | ¥150 |
| 100 早期 | ¥6,000 | ¥1,500 |
| 1,000 PMF 后 | ¥60,000 | ¥15,000 |
| 10,000 规模 | ¥600,000 | ¥150,000 |

**结论**：1000 用户后 LLM 线性成本碾压 embedding 固定成本。

---

## 3. 已定决策

### 3.1 不回滚原型词库扩充（2026-05-30）

**决定**：保留 strong_purchase 44 词 + consideration 22 词的扩充。
**Why**：用户决定"暂时先这样"，等真正改架构时一并处理。
**How to apply**：3 个 demo session 当前 funnel 显示 strong_purchase 偏高（特别是 ad9f759c 36.4%），demo 时不主动展开漏斗细节作为故事重点；如展开，主动加可信度免责声明。

### 3.2 修复 rerun-scoring 字段名错配 bug（2026-05-30）

**决定**：`backend/app/api/debug.py:131` 同时输出 `intent_type` 和 `intent` 两个 key，下游 `_build_semantic_summary` + `semantic_correlation` 才能正确聚合。
**Why**：原代码只写 `intent`，下游读 `intent_type` 全部 fallback 到 default `interaction`，funnel 永远 100% 互动。
**How to apply**：未来类似 records dict 跨边界传递时，必须 grep 下游读取字段名再写入；不要凭记忆。

### 3.3 意图分类整体优化退入 M2/M3（2026-05-30）

**决定**：M1 内测期 6-7 周不动意图分类系统。
**Why**：M1 用户量 5-10，不是分类准确率问题暴露的窗口；优先用户能用 + 能登录 + 能跨场比较；意图分类只在演示故事里轻提，不作为差异化武器。
**How to apply**：如果 M1 内测期用户反馈"漏斗数字明显不对"，优先按 § 4 路径走方案 B（LLM 标注机），不要回头做方案 A 5 层 Gate。

---

## 4. 未决问题（搁置中，触发条件时重启）

### 4.1 是否做 0.5 天 precision baseline 测量

**卡点**：用户决定 M1 期间不动这块，测量没有立即下游动作。
**触发条件**：M1 任一内测主播反馈"漏斗 / 转化数据明显不对"。
**届时动作**：抽样 6a1860b3 + 3 场清洁 pipeline session 的 strong_purchase precision 100 条，决定走方案 A 还是方案 B。

### 4.2 方案 A 还是方案 B 还是混合

**当前状态**：技术上推方案 B（LLM 当标注机 + 蒸馏到本地模型），但 M1 阶段不投入。
**届时动作**：
- M1 内测期（5-10 用户）：纯 LLM 分类，月成本 < ¥300，攒标注数据
- M2 早期（100 用户）：embedding + LLM 混合路由，confidence < 0.65 才送 LLM
- M3 PMF（1000+ 用户）：用 M1+M2 攒的标注数据微调 bge-small，回到固定成本

### 4.3 OCR 上游污染（混入主播话 / 字幕 / 教程）

**当前状态**：B-8 老 OCR 跑过的 session 没有 source_type 闸口，混入数据。新 session 走 source_classifier 闸口拦截 gift/system/unknown。
**触发条件**：B 阶段 anchor dashboard 上线后看跨场数据是否稳定；不稳定时回头查 OCR。
**届时动作**：在 ScriptSegment 旁边引入 "ASR 字幕 vs OCR 弹幕" 二次比对，把 OCR 抓到的"教学解说"识别出来过滤。

---

## 5. 下一步

**当前 M1 不安排任何意图分类相关工作。**

唯一例外：如果在 M1 任一阶段，主播反馈"漏斗数字看着不对劲"，**立即回到 § 4.1 触发条件**，先做 0.5 天测量再决定。

---

## 历史归档（2026-05-30）

- 本轮新增原型词：strong_purchase +29 词，purchase_consideration +10 词
- rerun-scoring bug 修复：debug.py:131 records dict 同时输出 intent_type + intent
- ad9f759c 阿野已 rerun-intent + rerun-scoring，funnel 已刷新（strong_purchase 36.4%，可信度低）
