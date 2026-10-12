# ADR-0001 · 间接表不冗余 owner_user_id 列

| 字段 | 值 |
|---|---|
| Status | Accepted |
| Date | 2026-05-31 |
| Decision-makers | owner + claude (A2 design review) |
| Migration that implements this | `alembic/versions/0008_data_isolation.py` |
| Helper that enforces this | `app/services/ownership.py` |

---

## Executive summary (4 行扫读版)

- **决定**：间接表（danmakus / script_segments / viewer_snapshots / pipeline_steps）不加 `owner_user_id`，业务 endpoint 必须先 `await get_owned_session()` 守口
- **触发 M2 升级**：B4 dashboard query > 3 层 JOIN / 单次响应 > 100ms / 间接表行数 > 10M
- **M2 升级路径**：ADD nullable → backfill FROM JOIN → ALTER NOT NULL → 扩 helper
- **M1 风险**：低（用户量 5-10，间接表行数 < 1M）

---

## Context

A2 数据隔离设计阶段，4 张主表（`live_sessions / anchors / session_notes / session_analyses`）确定加 `owner_user_id NOT NULL` 列 + ON DELETE CASCADE FK。

间接表是否也要加同样的列，曾在 A2 设计 review 里专门讨论：

| 间接表 | 行数（dev）| 关系 |
|---|---|---|
| `danmakus` | ~7,000 | 1 session → N danmakus |
| `script_segments` | ~3,900 | 1 session → N segments |
| `viewer_snapshots` | ~100 | 1 session → N snapshots |
| `pipeline_steps` | ~200 | 1 session → 11 steps |

owner 上 review 提出："这些会不会被 B4 dashboard 大量聚合？如果会，M2 大概率还是要加 owner_user_id 冗余列。"

---

## Decision

间接表**不**加 owner_user_id。所有业务 endpoint 在 query 间接表前必须经过一次 `await get_owned_session(session_id, ctx, db)` 守口，该 helper 已验证 ownership（含 admin bypass + 404-not-403）。

---

## Rationale

### 支持本决定的理由

1. **避免双写不一致**
   - 同一个 owner_user_id 在主表（authoritative）+ 间接表（冗余）两处存在，任何写路径漏更新一处就产生静默漂移
   - 真实生产事故的典型模式

2. **migration 复杂度爆炸**
   - 4 张主表三步走（ADD nullable → backfill → NOT NULL）已经写了 ~70 行 alembic
   - 4 张间接表再来一遍 = 双倍代码 + 双倍 backfill 时间（间接表行数 5x 主表）
   - 触发 ORM mixin 改造、batch 更新、potential lock contention

3. **M1 性能账算得通**
   - dev DB 总数据量 < 12,000 行
   - 内测 10 用户 × 30 场/月 × 3000 弹幕 = 900,000 行 / 月
   - JOIN `live_sessions ON session_id` 走主键索引 → P50 < 10ms
   - M1 整个生命周期（6-8 周）不会触达性能转折点

4. **逆向工程容易**
   - 加冗余列是 forward-only operation，M2 觉得需要时随时可加
   - 真加了再去掉就难（要 audit 所有写路径）
   - "可逆性"原则：从严到松（加）比从松到严（去）容易

### 反对意见 + 反驳

| 反对 | 反驳 |
|---|---|
| "B4 dashboard 一定大量聚合，现在不加 M2 必加" | 加 = 解决性能问题；不加 = 性能问题没出现。**当性能问题真出现时再加**，符合"避免预设计"。M2 触发条件（见下）已明确 |
| "JOIN 比直接 WHERE 慢" | 索引 JOIN 在 < 1M 行规模下不可测量。M2 真测出来再说 |
| "代码看起来更脏（每个 endpoint 都要先 get_owned_session）" | 这一行恰好是 A2 隔离设计的核心：endpoint 不调 get_owned_session = 没有 ownership check。把它 explicit 写在每个 endpoint 反而是清晰度的提升 |

---

## Consequences

### Positive

- ✅ Single source of truth：owner 信息只在主表，永远不会漂移
- ✅ migration 0008 写完即 done，不必扩展到 4 张间接表
- ✅ A2 ownership 单测/集成测试自动覆盖间接表（业务 endpoint 走 get_owned_session gate → 间接表 query 在 gate 之后）
- ✅ ORM mixin (`OwnedMixin`) 只挂 4 张主表，schema 改动收口小
- ✅ A2 Group 13.4 parametric invariant 一次扫所有 `/sessions/{id}/*` endpoint，免去为每个间接表单独写 invariant

### Negative / Trade-offs

- ⚠️ B4 anchor dashboard 跨场聚合时每次多一层 JOIN（`viewer_snapshots JOIN live_sessions ON session_id WHERE owner_user_id = ?`）
- ⚠️ 未来若出现"运维直接 `psql` 查间接表" 类需求，要主动 JOIN session（团队需培训）
- ⚠️ M2 若触发升级触发条件，要做 migration 0009，加冗余列 + backfill + 索引重建

---

## When to revisit (升级触发条件)

任一条件满足，启动 M2 升级到"间接表冗余 owner_user_id"模型：

1. **B4 dashboard 单 query JOIN 链 > 3 层** —— 主表 + 多个间接表多层 JOIN 慢得肉眼可感
2. **任一 endpoint 响应 P95 > 100ms 且根因是 JOIN** —— 火焰图 confirm 时间花在 JOIN 不是别处
3. **间接表行数 > 10M** —— 通常 1000+ 活跃用户 + 3 个月数据
4. **出现"按 owner 聚合 viewer_snapshots / danmakus" 模式 ≥ 3 个独立 endpoint** —— 业务上 owner 维度真常用

---

## Migration plan (M2 升级方案)

只在上面 4 条触发条件之一满足时执行：

```python
# alembic/versions/0009_indirect_table_owner.py

def upgrade():
    # Step 1: ADD COLUMN nullable on 4 indirect tables
    for tbl in INDIRECT_TABLES:
        op.add_column(tbl, sa.Column("owner_user_id", sa.String(36), nullable=True))

    # Step 2: backfill FROM JOIN live_sessions
    for tbl in INDIRECT_TABLES:
        op.execute(f"""
            UPDATE {tbl} t SET owner_user_id = ls.owner_user_id
            FROM live_sessions ls
            WHERE t.session_id = ls.id
        """)

    # Step 3: ALTER NOT NULL + FK + index
    for tbl in INDIRECT_TABLES:
        op.alter_column(tbl, "owner_user_id", nullable=False)
        op.create_foreign_key(...)
        op.create_index(...)

    # Step 4: write trigger to keep redundant column in sync on INSERT
    # (alternative: enforce via OwnedMixin.__before_insert__ at ORM layer)
```

配套修改：
- `OwnedMixin` 应用到 4 张间接表
- `apply_owner_scope` 扩展支持间接表（可选——也可以保持 JOIN 路径）
- 业务 endpoint 仍走 `get_owned_session` gate，但**间接表 query 现在可以直接 `WHERE owner_user_id = ?`**，省掉 JOIN
- 新 invariant `test_indirect_owner_matches_session_owner` 守冗余列不漂移

---

## Related

- migration `0008_data_isolation.py` —— 主表加 owner_user_id
- `app/services/ownership.py` —— `get_owned_session` / `get_owned_anchor` / `assert_owned_anchor` / `apply_owner_scope`
- `app/models/mixins.py` —— `OwnedMixin` 单点定义
- `tests/api/test_invariants.py` Group 13.4 parametric test —— 跨租户访问间接表的统一守口
- A2 设计 conversation：2026-05-31 owner + claude 多轮收敛
- [memory: project-a2-isolation](../../../.claude/...) —— A2 完整 ship 记录
