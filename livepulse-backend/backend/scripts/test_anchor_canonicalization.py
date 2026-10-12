import os
"""One-shot test of anchor canonicalization API.

Verifies:
  * " 小满 " (whitespace) collides with existing "小满"
  * "小满（美妆）" (full-width parens) is created
  * "小满(美妆)" (half-width parens) collides with the full-width one
Cleans up afterwards.
"""
import asyncio
import json
import httpx

from app.database import async_session_factory
from sqlalchemy import text

API = "http://127.0.0.1:8004/api/v1"
HEADERS = {"X-API-Key": os.environ["LIVEPULSE_API_KEY"]}  # 从环境变量读取,勿硬编码


async def post_anchor(c, name: str):
    body = json.dumps({"name": name, "platform": "douyin"}, ensure_ascii=False).encode("utf-8")
    r = await c.post(
        f"{API}/anchors/",
        headers={**HEADERS, "Content-Type": "application/json"},
        content=body,
    )
    return r


async def main():
    # 1) Cleanup any prior test artifacts
    async with async_session_factory() as db:
        await db.execute(text(
            "DELETE FROM anchors WHERE name = '??' OR canonical_name LIKE '%美妆%'"
        ))
        await db.commit()

    async with httpx.AsyncClient() as c:
        # 2) Existing anchor with name "小满" already exists
        r = await post_anchor(c, " 小满 ")
        print(f"POST ' 小满 ' → HTTP {r.status_code}")
        print(f"  body: {r.text[:300]}")
        assert r.status_code == 409, f"expected 409 collision, got {r.status_code}"
        print("  PASScorrectly rejected as duplicate")

        # 3) Full-width parens variant — new anchor
        r = await post_anchor(c, "小满（美妆）")
        print(f"POST '小满（美妆）' → HTTP {r.status_code}")
        print(f"  body: {r.text[:300]}")
        assert r.status_code == 200, f"expected 200 create, got {r.status_code}"
        full_width_id = r.json()["data"]["id"]
        print(f"  PASScreated with id {full_width_id[:8]}")

        # 4) Half-width parens — should collide with full-width via NFKC
        r = await post_anchor(c, "小满(美妆)")
        print(f"POST '小满(美妆)' → HTTP {r.status_code}")
        print(f"  body: {r.text[:300]}")
        assert r.status_code == 409, f"expected 409, got {r.status_code}"
        print("  PASScorrectly recognized as same anchor via NFKC normalization")

    # 5) Cleanup
    async with async_session_factory() as db:
        await db.execute(text(
            "DELETE FROM anchors WHERE canonical_name = '小满(美妆)'"
        ))
        await db.commit()
    print("cleanup done")


if __name__ == "__main__":
    asyncio.run(main())
