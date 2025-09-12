from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional

import pandas as pd

try:
    from constructor.models import Object as ObjectModel
except Exception:
    from document.models import Object as ObjectModel  # type: ignore

from .cache import load_pickle_df

@dataclass
class DataObject:
    orm: ObjectModel

    def _df(self) -> pd.DataFrame:
        """Return DataFrame for this Object using a cached loader."""
        path = getattr(self.orm, "data", None) or getattr(self.orm, "path", None) or ""
        return load_pickle_df(str(path))

    def get_record(self, record_id: str) -> Dict[str, Any]:
        df = self._df()
        if record_id in df.index:
            row = df.loc[record_id]
            if hasattr(row, "to_dict"):
                return dict(row.to_dict())
        # Fallback: try a column named 'id' or 0th column equals record_id
        if "id" in df.columns:
            m = df[df["id"].astype(str) == str(record_id)]
            if not m.empty:
                return dict(m.iloc[0].to_dict())
        return {}
