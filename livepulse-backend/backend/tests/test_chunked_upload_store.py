"""Unit tests for the pure helpers in chunked_upload_store.

The MinIO-backed functions (put_chunk / assemble_to_main / etc.) are
covered by the live end-to-end smoke against the running backend; here we
pin the pure logic that decides completeness + object naming.
"""
from app.services import chunked_upload_store as store


def test_missing_chunks_none_received():
    assert store.missing_chunks(set(), 3) == [0, 1, 2]


def test_missing_chunks_all_received():
    assert store.missing_chunks({0, 1, 2}, 3) == []


def test_missing_chunks_partial_sorted():
    assert store.missing_chunks({0, 2}, 4) == [1, 3]


def test_missing_chunks_ignores_extra_indices():
    # An out-of-range index slipping into received must not mask a real gap.
    assert store.missing_chunks({0, 1, 99}, 3) == [2]


def test_chunk_key_zero_padded_lexical_order():
    # Zero-padding is what makes lexical listing == numeric order during
    # assembly. If this padding ever shrinks, assembly silently reorders.
    keys = [store.chunk_key("u", i) for i in (0, 1, 10, 2)]
    assert sorted(keys) == [
        store.chunk_key("u", 0),
        store.chunk_key("u", 1),
        store.chunk_key("u", 2),
        store.chunk_key("u", 10),
    ]


def test_meta_key_namespaced_by_upload_id():
    assert store.meta_key("abc") == "abc/meta.json"
