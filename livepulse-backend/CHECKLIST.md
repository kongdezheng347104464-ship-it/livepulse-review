# Async Safety Checklist

Before modifying any async/pipeline code, verify:

## IO Operations
- [ ] No `requests` (use `httpx.AsyncClient`)
- [ ] No `time.sleep` (use `asyncio.sleep`)
- [ ] No sync `open().read()` on large files (use `aiofiles`)
- [ ] No sync `requests.put/post/get` on OSS (use `httpx`)

## CPU Operations
- [ ] No blocking CPU work in async code (use `asyncio.to_thread`)
- [ ] Local whisper always wrapped in `asyncio.to_thread`
- [ ] `OMP_NUM_THREADS` and `MKL_NUM_THREADS` set to 4

## Event Loop
- [ ] All async functions are `await`ed (not called without `await`)
- [ ] No `asyncio.run()` inside another event loop
- [ ] Pipeline runs in `async_session_factory` context

## Fault Isolation
- [ ] All optional steps return `SafeResult` (never None)
- [ ] All downstream consumers use `.get(key, default)`
- [ ] No `UnboundLocalError` risk from try/except blocks

## Contract
- [ ] Output conforms to schemas/events.py
- [ ] time_range included for replay capability
- [ ] evidence field present on insights
