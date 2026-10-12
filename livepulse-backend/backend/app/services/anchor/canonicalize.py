"""Anchor name canonicalization — single source of truth.

Purpose: prevent identity fragmentation at the moment of write. Two names
that a human would consider the same broadcaster should produce the
same canonical_name so the DB unique constraint catches them.

Design philosophy: be conservative. We DO NOT try to be clever (no
fuzzy matching, no edit-distance, no aliases). Anything that requires
human judgment ("tangtang" vs "小满"?) is OUT of scope — those collide
in business meaning but not lexically, and conflating them via heuristic
risks merging genuinely different broadcasters.

What we DO normalize:
  * Strip leading/trailing whitespace
  * Collapse runs of internal whitespace to a single space
  * Unicode NFKC normalization — folds full-width chars (Ｂ, （, ） )
    into their half-width equivalents (B, (, ))
  * Lowercase Latin letters — case is meaningless in identifiers; CJK
    chars are unaffected by .lower()

What we DON'T do:
  * "小满" vs "唐 唐" stay distinct — internal spacing might be intentional
  * "小满" vs "tangtang" stay distinct — cross-script aliasing is a human
    decision, not an algorithmic one
"""
import unicodedata
import re

_WHITESPACE_RE = re.compile(r"\s+")


def canonicalize_anchor_name(name: str) -> str:
    """Return the canonical form of an anchor name.

    Idempotent: canonicalize(canonicalize(x)) == canonicalize(x).
    """
    if not name:
        return ""
    # NFKC: full-width punctuation → half-width, compatibility-equivalent
    # chars folded. Crucial for "小满（美妆）" vs "小满(美妆)" — both fold
    # to the half-width form.
    s = unicodedata.normalize("NFKC", name)
    # Collapse all runs of whitespace (incl. non-breaking space, CJK
    # whitespace, tabs) to a single ASCII space — also strips edges.
    s = _WHITESPACE_RE.sub(" ", s).strip()
    # Latin chars only — Chinese has no case so this is a no-op on CJK.
    return s.lower()
