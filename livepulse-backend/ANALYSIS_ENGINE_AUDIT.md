# 分析引擎逻辑审计

> 审计目标：评估输出给主播的"指导意见"是否真实可信。
> 审计范围：`insight_engine/`, `script_analyzer/`, `danmaku_analyzer.py`, `correlation/`, `intent/`, `tactics/`, `live_rhythm_analyzer.py`, `pipeline._step_scoring`

---

## 🔴 致命级（结论可能误导主播）

### F-1. **三套评分公式并存且互相矛盾**

同名字段 `engagement_score / conversion_score / content_score` 在三个地方用三套公式计算，结果会不一样：

**(a) pipeline.py `_step_scoring`**（我本周新加的，给 list 卡片用）
```python
engagement_score = min(engagement_pm * 30, 100)                              # 互动密度 × 30
conversion_score = min(closing_total / duration_min * 60 * 4.2, 100)         # 逼单密度 × 4.2
content_score = script_score                                                  # 话术分均值
```

**(b) `insight_engine/engine.py:39-42`**（给详情页 insight_report 用）
```python
engagement_score = min(100, eng.get("total_prompts", 0) * 3)                 # 互动总次数 × 3
conversion_score = min(100, closing.get("total_tactics", 0) * 5)             # 逼单总次数 × 5
content_score = min(100, pain.get("total_mentions", 0) * 3)                  # 痛点提及次数 × 3 ❓
```

**(c) `semantic_correlation._conversion_score`**（再算一遍单段转化分）
```python
score = lift * 20 + strong * 3 + consideration * 1.5 + (2 if tactic else 0)
```

**问题**：
1. 同一个 session 在列表卡和详情页可能显示不同的"互动分"
2. 公式 (b) 的 `content_score = 痛点提及 × 3` 在概念上就是错的 —— "主播提了多少次痛点" ≠ "内容质量好"。一个无脑堆砌"皮肤干、皱纹深、毛孔粗"的主播能拿满分
3. 三组数字给主播的指导方向可能矛盾（公式 (a) 看密度，公式 (b) 看绝对量；3h 直播绝对量必然高，1h 直播必然低，但密度可能相反）

---

### F-2. **`composite_score` 大多数情况下就是话术分**

`pipeline.py:511-515`：
```python
if ctx.danmaku_result and ctx.danmaku_result.get("total", 0) > 0:
    danmaku_score = min(total / duration_sec * 200, 100)
    composite = script_score * 0.6 + danmaku_score * 0.4
else:
    composite = script_score   # ← 你 9 个 session 里 8 个走这条
```

OCR 几乎全部 skip 了（9 个 session 只有 2 个有 danmakus，其中 1 个只有 102 条）。所以"综合评分"实际上只反映了**主播说话的话术结构**，没反映任何观众反应。

主播看到的"综合 43.5 分"完全没有"观众是否买账"这一维度，但 UI 上叫"综合评分"，**对主播是误导**。

---

### F-3. **engine.py 的三档评分门槛太松，几乎所有直播都满分**

```python
engagement_score = min(100, total_prompts * 3)    # 34 次互动 = 100
conversion_score = min(100, total_tactics * 5)    # 20 次逼单 = 100
```

对 3h 直播：
- 34 次互动 = 11/小时 = 0.18/分钟 — 任何主播都能做到
- 20 次逼单 = 6.7/小时 — 同样轻松

实际数据验证（你 9 个 session）：所有 conversion_score 都 ≥ 31，半数 = 100。**分数失去区分度**，没法告诉主播"你比上次进步了还是退步了"。

---

### F-4. **`drop_segments` 定义和"低互动"语义脱节**

`pattern_extractor.py:90`：
```python
if b["danmaku"] == 0 and b["engagement"] == 0 and b["closing"] == 0:
    drop_segments.append(...)
```

你的 OCR 大多 skip → 大多数 30s bucket `danmaku == 0`。所以一个**正常讲解中**、有话术内容但 OCR 没采到弹幕的时段会被判为"drop"。

action_recommender 又会基于这个 drop 数量推"建议在低互动时段插入福利预告"。**主播会拿到错误的优化方向**。

---

### F-5. **SnowNLP 对直播弹幕几乎不可用**

`danmaku_analyzer.py:14-29` 用 SnowNLP 做情感分析。SnowNLP 训练数据是 2013 年新浪微博 + 图书评论。

美妆直播弹幕："姐妹冲""家人们买它""yyds""绝绝子""xs""芭比 Q 了" —— **SnowNLP 几乎全部分类为 neutral**（关键词不在词典里）。结果：sentiment_distribution 失去意义，弹幕几乎都被打成中性。

---

### F-6. **`detect_purchase_intent` 反例情况打满**

`danmaku_analyzer.py:32-39`：
```python
def detect_purchase_intent(text):
    for kw in PURCHASE_INTENT_KEYWORDS:
        if kw in text_lower:
            return "purchase_intent"
```

`PURCHASE_INTENT_KEYWORDS` 包含 "下单"、"购买"、"想要"。但：
- "我现在不想下单" → 命中 → 算"购买意图"
- "下单流程太复杂了" → 命中 → 算"购买意图"
- "1号" 既可能是用户表达对 1 号商品感兴趣，也可能是无关序号

✅ 好消息：embedding 的 intent_analyzer 在 pipeline 里覆盖了这个 keyword 结果。但 `analyze_danmakus` 返回的 `purchase_intent_count` 字段仍然来自关键词版本，被下游一些地方继续用。

---

## 🟡 严重级（指导可能跑偏）

### W-7. **correlation 窗口 10 秒太短，漏掉真实关联**

`semantic_correlation.py:34`：`window_sec=10`。

现实路径：主播说完话 → 观众看 → 思考 → 打字 → 发送 → 弹幕飘到屏幕上 → OCR 30s 抓帧 → 写入 DB。**端到端延迟通常 15-40 秒**。10 秒窗口会大量漏掉真正的"话术触发反应"对。

建议 `window_sec=30~45`。

---

### W-8. **`_significance_score` 公式系数全部凭空**

`ranking.py:151-186`，5 维加权 + 最后 `min(int(raw / 2), 100)`。
- 为什么 `/2`？无文档
- `vol_raw = dm * 1.5 + eng * 2 + purchase * 8 + closing * 3` —— 这 4 个系数怎么来的？
- 权重 {30, 20, 25, 15, 10} 后又在子项里做 `WEIGHT_VOLUME * 3` 的 cap

整体不可解释。同样输入跑出 75 分还是 60 分没人能解释为什么。

---

### W-9. **"hot segment" 阈值 `1.5 × 平均` 统计上太弱**

`pattern_extractor.py:67`：
```python
dm_threshold = max(dm_avg * 1.5, 2)
```

均匀分布下约 30-40% 的 bucket 会超过 1.5×均值 → "hot segment" 太多 → 失去突出性。**正确的"热点"检测**应该用：
- 中位数 + MAD 异常值
- 或 95 百分位
- 或 Z-score > 1.5

---

### W-10. **`action_recommender` 有条建议永远会触发**

`action_recommender.py:45`：
```python
if transitions > phases * 0.8 and phases > 5:
    recs.append("减少话题切换频率...")
```

`phase_transitions == max(len(phases) - 1, 0)`（rhythm_analyzer.py:171）。所以 `transitions == phases - 1`，**只要 phases > 5 就恒满足 transitions > phases * 0.8**。

这条"话题切换过频"的建议对每场超过 5 个 phase 的直播都会推出来，不是数据洞察，是 bug。

---

### W-11. **`baseline rec` 假装是数据分析结果**

`action_recommender.py:86-91`：
```python
recs.append({
    "priority": "low",
    "category": "综合",
    "action": "保持每场直播至少3次价格锚点话术",
    "reason": "价格对比是触发购买意向的最有效手段之一",
})
```

这是无条件追加的硬编码"心灵鸡汤"，跟当前 session 数据无关。但混在数据驱动的 recommendation 里返回，主播以为是分析得出的。

---

### W-12. **关键词级冲突 — "1号" 同时是购买信号 + 商品序号**

`PURCHASE_INTENT_KEYWORDS` 里有 "1号", "2号", "3号" 也有 "链接"。
- 主播：「1号链接是xxx」→ 没有 keyword 检查上下文，会有 false positive
- "链接" 在弹幕里出现可能只是请求别人发链接，不一定本人想买

---

## 🟢 中等级（数据完整性 / 表达问题）

### M-13. **segment 得分有 4 套来源**

同一个 segment 上挂着：
1. `score`（segment_scorer.score_segment，类别 base + 长度 + 速率 bonus）
2. `analysis_tags.embedding_tactics[*].strength`
3. semantic_correlation 单独再算的 `conversion_score`
4. insight_generator 给 narrative 算的 `score`

四套都在不同维度，但 UI 上同一个 segment 卡片可能会展示其中一两个，**主播很难理解"这段到底是好是坏"**。

---

### M-14. **opening_detector 只看前 180 秒**

`opening_detector.py:21`：`max_start = 180`。

主播经常**头 30s 寒暄、闲聊"姐妹们好"，180s 之后才进入正式开场介绍**。这种情况 opening 会被部分判中或干脆没判中。

---

### M-15. **content_score 概念错位 vs 跨场对比缺失**

没有跨场对比 baseline 是这个系统**最大的产品缺陷**。所有评分都是绝对值：
- "你这场互动得 67 分" — 67 是好是坏？不知道
- 没有"你上场 75，这场 67 —— 退步了"
- 没有"行业平均 65 — 你略高于平均"

对主播来说真正可指导的是**相对比较 + 趋势**，不是绝对分数。

---

### M-16. **跳过 OCR 时该明确告知主播：弹幕维度不可信**

当前 9 个 session 几乎全部 OCR skip，但 UI 不会告诉用户："此场未采集到有效弹幕数据，所有'观众反应'相关指标仅供参考"。**用户拿着 0 弹幕数据生成的"互动分"做决策是危险的**。

---

## 📋 优先级修复建议

| 优先级 | 项 | 修法 |
|--------|---|------|
| **必修** | F-1 三套评分公式 | 统一为一处，删掉 engine.py 内的版本，明确文档化 |
| **必修** | F-4 drop_segments 误判 | 加判定：仅在 `ctx.ocr_raw` 非空时才计算 drop |
| **必修** | F-6 detect_purchase_intent 误判 | 删掉这个老函数；只用 embedding 版本 |
| **必修** | W-10 action_recommender 恒触发 bug | 重写阈值判断逻辑 |
| **必修** | M-16 OCR skip 时数据标注 | analysis_summary 加 `data_completeness` 字段 |
| 应修 | F-3 评分门槛过松 | 系数全部需要校准（拿 10 场真实数据回归） |
| 应修 | F-5 SnowNLP 不可用 | 换成 embedding 直接做 sentiment |
| 应修 | W-7 correlation 窗口扩大到 30-45s | 一行改 |
| 应修 | W-11 baseline rec 撤掉 | 不应假装是分析结果 |
| 中期 | F-2 composite_score 公式 | 重新设计，OCR skip 时不让"综合分"误导 |
| 中期 | M-13 segment 分数统一 | 选一种 score 当 canonical |
| 中期 | W-8/W-9 ranking & threshold | 换成统计学方法（百分位 / Z-score） |
| 长期 | M-15 跨场对比 + 趋势 | 真正能指导主播的核心功能，需要 multi-session 数据库视图 |

---

## 总结

**当前分析引擎更像"基于关键词的话术摘要器 + 规则化建议清单"，离"给主播提供可靠指导意见"还有距离。**

核心问题：
1. **公式不可解释**：评分都是凭空的系数加权，没有训练数据校准
2. **指标不可信**：OCR skip 让"互动/转化"维度直接缺失，但 UI 不告知
3. **建议不可信**：硬编码 + bug 触发 + 关键词误判混在一起
4. **没有 baseline**：所有数字是绝对值，主播看不出"好或坏"

如果你的目标是真给主播提供指导，建议优先级：
1. 先修上面"必修"5 项（半天工作量）—— 让现有数字不再误导
2. 然后做 **multi-session 对比视图**（M-15）—— 这才是给主播的真正价值
3. 然后回头校准评分公式（拿历史数据做线性回归）
