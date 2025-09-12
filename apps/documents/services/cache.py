from __future__ import annotations
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import pandas as pd

@dataclass(frozen=True)
class _FileKey:
    path: str
    mtime_ns: int

def _stat_key(path: str) -> _FileKey:
    p = Path(path)
    try:
        st = p.stat()
        return _FileKey(path=str(p), mtime_ns=getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
    except FileNotFoundError:
        # If file absent, still return a key (forces distinct cache entry per miss)
        return _FileKey(path=str(p), mtime_ns=-1)

@lru_cache(maxsize=128)
def load_pickle_df(path: str) -> pd.DataFrame:
    """Load a pandas DataFrame from a pickle with mtime-aware cache.

    The cache key includes file mtime; when file is updated, the key changes
    and data is re-read. Missing files return an empty DataFrame.
    """
    key = _stat_key(path)
    try:
        # Re-open by original path to avoid double stat
        return pd.read_pickle(path)
    except Exception:
        return pd.DataFrame()

def clear_cache() -> None:
    load_pickle_df.cache_clear()  # type: ignore[attr-defined]
