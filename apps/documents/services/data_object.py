"""DataObject: interface to dynamic data for templates.

The ``DataObject`` class hides the details of how data is stored and
retrieved for a given ``Object`` from the database. Currently it
implements loading from a pickled ``pandas.DataFrame``. It exposes
methods to load a single record by identifier, returning a mapping of
parameter names to values. This encapsulation makes it easy to
introduce alternative storage mechanisms (e.g. MongoDB, API calls)
without changing template rendering logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd
from constructor.models import Object as ObjectModel, Parameter as ParameterModel

__all__ = [
    "DataObject",
]


@dataclass
class DataObject:
    """Encapsulate data for an Object.

    Instances of this class are created with a Django ``Object`` model
    and provide methods to load data from its storage. Data is loaded
    lazily and cached for reuse within the same request.
    """

    orm: ObjectModel
    _df_cache: Optional[pd.DataFrame] = None
    _id_col_name: Optional[str] = None

    def _load_df(self) -> pd.DataFrame:
        """Load and cache the DataFrame from the object's data file.

        If the object does not define a path, an empty DataFrame is
        returned. The result is cached to avoid repeated disk I/O.
        """
        if self._df_cache is None:
            path = getattr(self.orm, "data", None)
            self._df_cache = pd.read_pickle(path) if path else pd.DataFrame()
        return self._df_cache

    def _ensure_id_column(self) -> str:
        """Determine and cache the identifier column for records.

        The identifier column is chosen in priority order:
        1. ``id_to_connect`` if present in the data frame.
        2. A column named after the identifier parameter's id.
        3. A column named after the identifier parameter's name.
        4. ``id_to_connect`` if present as a fallback.
        5. The first column of the DataFrame or ``_index`` if empty.
        """
        if self._id_col_name:
            return self._id_col_name
        df = self._load_df()
        ident_param = (
            ParameterModel.objects.filter(object=self.orm, identificator=True).first()
        )
        if ident_param:
            if "id_to_connect" in df.columns:
                self._id_col_name = "id_to_connect"
            elif str(ident_param.id) in df.columns:
                self._id_col_name = str(ident_param.id)
            elif ident_param.name in df.columns:
                self._id_col_name = ident_param.name
        if not self._id_col_name:
            self._id_col_name = (
                "id_to_connect"
                if "id_to_connect" in df.columns
                else (df.columns[0] if len(df.columns) else "_index")
            )
        return self._id_col_name

    def get_record(self, id_value: Any) -> dict:
        """Retrieve a record by its identifier.

        Parameters
        ----------
        id_value: Any
            The value of the identifier column to search for. This may
            be a string or numeric value. If the identifier is stored as
            a different type, a string comparison is attempted.

        Returns
        -------
        dict
            A mapping of parameter names to their values for the
            matching record. If no record is found, an empty
            dictionary is returned.
        """
        df = self._load_df()
        col = self._ensure_id_column()
        # Handle the case where we fallback to row index
        if col == "_index":
            try:
                row = df.iloc[int(id_value)]
            except (ValueError, IndexError, KeyError):
                return {}
        else:
            subset = df.loc[df[col] == id_value]
            if subset.empty:
                # attempt string comparison if the types differ
                subset = df.loc[df[col].astype(str) == str(id_value)]
            if subset.empty:
                return {}
            row = subset.iloc[0]
        # Build a mapping from parameter names to values. A column may be
        # either named by parameter id or parameter name.
        mapping: dict = {}
        for param in ParameterModel.objects.filter(object=self.orm):
            if str(param.id) in row:
                mapping[param.name] = row[str(param.id)]
            elif param.name in row:
                mapping[param.name] = row[param.name]
        return mapping
