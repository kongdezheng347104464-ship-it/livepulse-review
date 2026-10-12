# Code Review Checklist

## Quick Scan (2 min)

- [ ] Does this change touch pipeline / async / state / event schema / insight engine?
- [ ] Is there any new `import requests`, `import time`, or sync file IO?
- [ ] Does any new code return `None` where a list/dict is expected downstream?
- [ ] Are there any bare `except:` without specific exception types?
- [ ] Does this change modify the event schema or analysis_summary structure?

## Pipeline Changes (if applicable)

- [ ] Are all 8 steps still tracked in `step_tracker.STEP_ORDER`?
- [ ] Does the new step follow `SafeResult` pattern?
- [ ] Does it degrade gracefully on failure?
- [ ] Is it recorded in `pipeline_steps` table?

## Frontend Changes

- [ ] Does any new chart component use `<BaseChart>` instead of raw `<ReactECharts>`?
- [ ] Are analysis functions in `src/analysis/` rather than inline in components?
- [ ] Does the component read from `useSessionStore` instead of prop drilling?
- [ ] Click handlers on charts call `setFilters` for cross-chart interaction?

## Approval Gates

- [ ] `tests/regression.py` passes before merge
- [ ] Backend: `python -c "from app.tasks.pipeline import run_analysis_async"` succeeds
- [ ] Frontend: `npx vite build` succeeds
- [ ] No credentials in code, comments, or commit messages
