# Retrospective · 2026-05-29 下午

**阶段**：scoring v5 收尾验证 → 全链路 QA 大审 → P0 修复轮次 → 长视频实测
**人**：owner（作者） + Claude（QA / 系统工程 / 实施视角）
**起点**：用户想做 "归一化指标接入 composite 收尾验证"
**终点**：32 条 bug → 7 个 commit 修掉 5 条 Critical + 2 条新发现，long-video 实测用户说 "**自动翻到分析页了**"

---

## 0. 这一轮做完了什么（事实清单）

| 类别 | 内容 |
|---|---|
| 文档 | `docs/qa-upload-pipeline-audit.md`（32 条 bug 完整 QA 报告，17KB） |
| 文档 | `docs/retrospective.md`（这份） |
| 代码 commit | `fe69822` P-1 step_tracker flush→commit |
| 代码 commit | `831368f` B-3/B-6 numpy/transformers/Mac path 全链 |
| 代码 commit | `2cb0221` B-8 5 阶段诊断日志 |
| 代码 commit | `b705c06` R-6 无弹幕时 suppress 假洞察 |
| 代码 commit | `25ff301` P0-8 4 条 invariant 新增 |
| 代码 commit | `25ad755` R-13 session.status 单 gate |
| 代码 commit | `bb1369f` R-14 viewer_count 中文 + R-15 漏斗 0 除法 |
| 实测 | 短视频 3.5min + 长视频 11min 两次端到端 |
| 实测花费 | OCR/ASR ≈ 5-10 元 |
| 总耗时 | ≈ 半天 |

**用户体验前后对比（实证）**：
- 第一次 QA（22 分钟 0% 不动）：**"我以为系统死了，想关掉 tab"**
- P0-9 11min 实测：**"自动翻到分析页了，全程无红字"**

---

## 1. 哪些决策是对的（值得复用）

### 1.1 用户主张"先做完整 QA 报告，不要先修 bug"
**事实**：决定走 A 路线（"代码 + 历史数据 + Preview UI 审计"+ 完整端到端 1h15min 实测，不修 bug），换来 32 条 bug 完整图谱。

**为什么对**：
- B-3（nn）和 B-8（danmaku 丢）的因果链只在端到端实测才暴露
- R-6 假洞察这种 "AI 产品最危险" 的失败模式靠静态审计抓不到
- 修了一两条 P0 之后再测，状态空间复杂度上升，再发现新 bug 时分不清是新引入还是原有
- 写报告本身强迫"严重度 + 复现步骤 + 修法"三位一体思考，比临时 hack 健康得多

**下次复用**：进入一个不熟悉的产品 / 接手一段代码 / 上线前的 readiness review，**永远先报告，再修**。

### 1.2 用户提出"5 分钟修 P-1 收益大于其他所有 bug 总和"
**事实**：用户在我的 QA 报告基础上做出关键判断 ——
> "系统在正确工作时，用户仍然认为系统死了" = 比"系统真错了"更危险

**为什么对**：实际验证了。改一行 `flush → commit` + 前端早就消费 steps → 长视频实测进度条真推进、阶段文案切换。**单刀效益最大化**。

**下次复用**：QA 报告里如果出现"5 分钟改动 vs 改变第一印象"的组合，永远先做。

### 1.3 我 push back 4 处修复顺序
**事实**：用户给了 5 步顺序，我提出 4 处修正 ——
- (a) P-1 单独修可能不够，要查前端是否消费 steps
- (b) B-6 应该最先做，可能连带消 B-8
- (c) "再跑一次"不是免费，先小后大
- (d) 每个修复配 invariant

**为什么对**：
- (a) 担心方向虽然错（前端早就消费了），但 audit 是免费保险
- (b) B-6 调查穿透到 numpy/transformers 版本不匹配，发现真根因
- (c) 短视频 + 长视频两次跑，省了 OCR 钱也按"快场景测 race、慢场景测大批量"分工
- (d) 4 条新 invariant 当场抓到 ad9f759c 的 B-8 副作用 + R-6 假洞察

**下次复用**：push back 时给具体理由 + 可执行替代，而不是"我觉得不对"。

### 1.4 接受 invariant 红 = 真实状态
**事实**：P0-8 写完 4 条新 invariant，34→38 跑通时 2 条 fail。第一反应是想跳过 ad9f759c。最终决定**保留红**，理由：invariant 不是为了好看，是为了反映系统真实状态。

**为什么对**：长视频测试后新 session 全绿，ad9f759c 仍红 = 信号清晰准确。"绿了 36 + 红了 2（已知脏数据）" 比 "强行全绿，掩盖 ad9f759c 的真问题" 健康。

**下次复用**：测试红 ≠ 测试失败。先判"红得对不对"，再判"要不要修"。

### 1.5 用户拒绝实时切换协作模式
**事实**：用户中途说"切来切去太烦"。我提了 3 个替代方案（A 跳过 / B Claude in Chrome / C 一次性观察）。最终走"C: 一次性观察 + 3 张截图 + 后端日志 + DB"协作。

**为什么对**：用户独立完成 11min 测试，3 张截图 + 1 句话总结 = 我接到完整信号。零中间打断。**用户的累带来的疲劳错觉是真信号，不是抱怨**。

**下次复用**：长任务里如果用户开始抱怨流程，先问"这次需要的信号是连续观察 vs 终态对比？"，不要默认"实时反馈最有效"。

---

## 2. 哪些走了弯路（值得避免）

### 2.1 在 hf-mirror 模型下载上浪费 30+ 分钟
**事实**：修 B-6 时，我执着于"让模型真能下下来"。试了 cdn-lfs、HF_ENDPOINT、镜像 308 跳转分析，最后还是没下成。

**为什么错**：
- embedding_service 早就有 graceful fallback：模型不可用就走 keyword-only tactic 识别
- 真正需要修的是 "B-3 nn 错误不再出现" 这个根因，跟模型能否下没必然关系
- 后续短视频测试证明：**embedding 不可用时整条 pipeline 跑通了**，只是 tactic 识别精度降级（data_completeness banner 也已经透明告诉用户）

**下次怎么做**：调查 bug 时先列"修了之后什么不一样、不修又什么不一样"。如果不修也能 graceful 退化，**先承认 graceful，把精力留给硬阻塞**。

### 2.2 R-13 race condition 在 P0-1 commit 时本可预见
**事实**：P-1 修复（flush→commit）让 step 状态在 pipeline 跑的过程中变得对其他 transaction 可见。**这同时让 intermediate analysis_summary 也可见**。前端 gate 用 `progress.status || session.status` —— progress 先翻 analyzed，session 还没刷新 —— UI 撕裂。

**为什么错**：写 P-1 commit 时我已经在注释里写了 "Side effect (acceptable): commit also persists any rows that the pipeline step queued"。这是 commit 边界 + 数据可见性的根本变化。**当时应该立刻问"前端任何地方依赖 session/analysis_summary 的原子性吗？"**，但我没问。

**下次怎么做**：
- 改变事务边界 / 数据可见性 / 状态同步语义的 commit 必须配一个 mental checklist：
  - "谁读这块数据？"
  - "他们依赖原子可见性还是允许 partial？"
  - "改后会不会有撕裂态？"
- 这种 commit 不要单独 ship，**最好同时改读侧的 defensive 渲染**

### 2.3 P0-2 担心方向不对
**事实**：我担心前端 hardcode "准备中"，需要专门改前端消费 steps。实际查代码发现 `ProgressOverlay.jsx` 早就有完整 `STEP_LABELS` 映射 + `running.name` 查询。

**为什么错（其实没那么错）**：审计是免费保险，所以 P0-2 不算"白做"。但**预设"前端肯定没消费"** 是不该有的偏见。

**下次怎么做**：审计时 hypothesis 写中性 —— "我要确认 X，可能 Y，也可能 Z"，不要 "我担心 Y" 这种带方向的偏见。

### 2.4 升级 transformers 时连带升 huggingface_hub 没看清
**事实**：`pip install "transformers>=4.46,<4.50"` 自动把 huggingface_hub 升到 0.36.2。0.36 vs 0.22 的 redirect 处理不一样，可能就是模型镜像下载失败的二级原因。

**为什么错**：装包时只盯主依赖版本，没看 pip 自动 resolution 的二级依赖。

**下次怎么做**：装关键依赖前先 `pip install --dry-run` 看完整 resolution 列表。或者用 lockfile（uv / poetry）锁死间接依赖。

### 2.5 P0-4 commit 包含太多无关改动
**事实**：commit `831368f` 同时改了 5 件事 —— path / HF_ENDPOINT / exception logging / requirements 加 pin / numpy+transformers 降级。commit message 写了 30 行才讲清楚。

**为什么错**：违反"每个逻辑单元单独 commit"。如果 numpy 降级出问题想 revert，会连同 path/mirror/logging 一起回滚。

**下次怎么做**：
- 路径/环境改动 = 1 个 commit
- exception logging 风险升级 = 1 个 commit
- 依赖版本 pin = 1 个 commit
- numpy 降级 = 1 个 commit
每个 message 一句话能讲完 = good。

---

## 3. 下次类似情况怎么做（操作守则）

### 3.1 production readiness review 的标准协议
按本次复用：
1. **先全面审计，不修任何 bug**（避免边修边出新症状）
2. 报告分级：Critical / High / Medium / Low（用户感知 + 数据完整性两个维度独立评分）
3. **每个 bug 复现步骤 + 根因 + 修法 + 工作量预算**（缺一项就不上报告）
4. **用户原话引用至少 1 条** —— "我以为系统死了" 比任何技术描述都有说服力
5. 报告末尾必须有"是否达到生产可用水平 yes/no + 一句话理由"

### 3.2 P0 修复轮次的操作模板
1. **修复前 push back 顺序**：列原因，最好能用"如果不按这个顺序，浪费 X 小时" 量化
2. **每修一个配一条 invariant**：写 test 的人是修 bug 的人，不能分给别人
3. **commit 颗粒度**：每个 commit 一句话 = 不超过 10 字的 verb phrase。超出 = 拆。
4. **修完跑 invariant**：拒绝"绿了再修"心态，红是诊断信号
5. **挑场景**：短场景（容易看 race）+ 长场景（容易看大批量）至少各 1 次端到端

### 3.3 协作疲劳的早期识别
本次教训：用户说"切来切去太烦"是流程问题不是用户问题。
- 看到这个信号立刻**列 2-3 个替代协作模式**给用户选
- 不要默认"实时来回是最佳"
- 大任务里**预先**问一次"你想全程实时反馈还是终态对比"

### 3.4 涉及钱 / 真数据 / 长耗时操作必问
本次 OCR/ASR 5-10 元的总成本 ≈ 1 杯咖啡，但**每次跑前问一句**是规则。
- 跑前先问"接受 X 元成本 + Y 分钟时间吗？"
- 跑后**主动报账**："实际花了 X 元，比预算 Y 多 / 少 N%"

---

## 4. 悬案：B-8 在大数据量下的真实行为

**事实陈述**：
| session | events | 持久化结果 | code 路径 |
|---|---|---|---|
| ad9f759c (QA 1h15) | 581 speech | **0 入库**（全丢） | pre-fix（flush 不 commit） |
| 114f5081 (短测 3.5min) | 14 speech | 14/14 ✅ | post-fix（commit per step） |
| c985bd31 (P0-9 11min) | 113 speech | 113/113 ✅ | post-fix |

**问题**：B-8 在 581 量级真的会丢，还是 ad9f759c 那次是 transient？P0-1 commit-per-step 之外还有没有别的因果？

**已知不可能因素**（已排除）：
- ❌ session 隔离问题 — 改 commit 后 113 量级也成功
- ❌ embedding 不可用 — 113 量级 embedding 同样不可用但全过
- ❌ source_classifier 把 581 全标 unknown — 日志明确写 `speech=581`

**潜在因素**（未验证）：
- (a) `db.add()` 在 ~500+ 量级时 batch 触发 SQLAlchemy 隐式 flush 失败（事务大小？）
- (b) `asyncio.to_thread(analyze_danmakus, 581 件)` 占用 event loop 太久导致 timeout
- (c) pre-fix 代码路径存在某个 only-large-batch 边界 bug，post-fix 隐式修了
- (d) 真的就是 transient（OCR 在那刻和阿里云连接抖动？）

**当前策略**：等下次有真实 500+ events 上传发生，记录 `[danmaku.dbg]` 5 阶段日志，再判断走 (a)/(b)/(c)/(d)。
- 如果再发：日志会精确告诉我们在哪一 phase 死
- 如果不发：归档为 transient

**记入 P2**：不主动调查，被动等触发。

---

## 5. 长尾未修（按优先级）

### High（不修也能上，但每个会持续刷存在感）
- R-4 漏斗右下角神秘"总弹幕 1"——前端某处凭空数字
- R-7 顶部"可信度 84%·高可信" 计算因子不透明
- R-10 关键洞察 50/44/42 数字没标签
- U-2 上传页无格式 / 大小 / 时长边界提示
- U-6 标题 placeholder "自动使用文件名"实际不填
- U-10 新建已存在主播会失败（应 inline 提示并切换）

### Medium（体验粗糙有解法）
- U-1 拖拽上传不响应
- L-3 Google Fonts 慢加载导致字体闪
- L-4 多场"测试"标题无主播 chip 区分
- R-9 漏斗下方空白未做 empty state
- R-11/R-12 关键洞察数字与标签横向距离 + 展开字号

### Low（cosmetic）
- L-1 favicon 404
- L-2 React Router v7 future flag warning
- L-5 综合分整数 vs 小数格式不一致
- P-3 antd message context warning
- L-6 hover 删除按钮无 confirm（待验证）

**何时清**：下一轮"用户体验冲刺"统一做。今天目标是产品**可信**，不是好看。

---

## 6. 对系统工程文化的反思

**用户给出的判断**值得抄进我的工作守则：

> 不是慢，而是"看起来像完全没在工作"。这和 22 分钟、45 分钟、2 小时都没关系。只要 UI 一直 "0% 准备中"，用户就默认"坏了"。

→ **AI 产品允许慢，但必须持续"活着"。**

> 没洞察 = 系统还不成熟，假洞察 = 系统不可信。

→ **数据缺失时降级为空，永远不要伪造洞察。**

> 系统真实状态与用户感知状态完全脱节，是最危险的失败模式。

→ **可观测性是产品功能，不是工程辅助。**

这三句以后写进 `~/.claude/CLAUDE.md` 的全局规则里。

---

## 7. 致谢 / 总结一句话

> 这一轮的核心收获不是修了 5 个 critical bug，而是确立了 "QA 报告 → 集中 P0 修复 → 短长两场实测 → 每修配 invariant" 的标准化 production readiness 流程。**这个流程比这次的具体 bug 都重要**。

下次新模块上线 / 大重构后 / 真实用户测试前 ——
**重复这个流程。**

---

**生成时间**：2026-05-29
**下次复盘触发条件**：(a) B-8 在 500+ 量级再次复现 (b) 完成一次真实主播试用 (c) 上线生产 (d) 又一轮大重构后
