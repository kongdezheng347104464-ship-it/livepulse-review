"""Script analyzer utilities."""
import json
from pathlib import Path
from functools import lru_cache

_CONFIG_DIR = Path(__file__).parent.parent / "config"


@lru_cache(maxsize=16)
def load_json_config(name: str) -> dict:
    """Load a JSON config file from the config directory."""
    path = _CONFIG_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache()
def load_stopwords() -> set[str]:
    """Load stopwords from config/stopwords.txt."""
    path = _CONFIG_DIR / "stopwords.txt"
    if not path.exists():
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return {w.strip() for w in f.read().split() if w.strip()}
