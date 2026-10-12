# LivePulse 项目级 Claude 指令

> 项目内部代号 WorkBuddy，对外品牌 LivePulse / 脉播。
> 本文件与 `~/.claude/CLAUDE.md`（全局规则）共同生效，冲突时本文件优先。

## 项目上下文

- **产品定位**：小主播 / 小 MCN 的"教练型 AI 直播复盘"SaaS
- **核心承诺**："每一场都有迹可循"——别再熬夜看回放，复盘只要 10 分钟
- **Stack**：FastAPI + PostgreSQL + MinIO + Redis + Celery（后端）；React + Vite（frontend_v2 为主推，端口 3002）；Qwen via DashScope（LLM）；阿里云 OCR/ASR
- **测试基线**：`cd backend && PYTHONPATH=. ../.venv/bin/pytest -q`，全部通过才算完成

## 项目专属约束

- **品牌**：用户可见的文字必须用 LivePulse / 脉播；工程内部（Docker、cache、git）保留 WorkBuddy
- **ADR-0002**：重分析/rerun 失败不得摧毁上次好结果
- **ADR-0003**：按主播类型评估；教学型主播禁止给带货建议
- **测试隔离**：绝不对真实 demo session（41e9e83d / b456b873 / ad9f759c / 935ca358）做 rerun / destroy
- **OCR pipeline**：三段并行优化（Day1~3）已落地，改 pipeline 时保护 `asyncio.Lock` 和 `extract_all_per_frame`

---

## Andrej Karpathy Coding Guidelines

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

*Karpathy guidelines sourced from [forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills)*
