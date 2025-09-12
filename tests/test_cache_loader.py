import types
import pandas as pd

from apps.documents.services.cache import load_pickle_df, clear_cache

def test_load_pickle_df_mtime_cache(tmp_path):
    p = tmp_path / "data.pkl"
    df1 = pd.DataFrame({"id": ["1"], "name": ["A"]}).set_index("id")
    df1.to_pickle(p)
    a = load_pickle_df(str(p))
    assert a.equals(df1)

    # Overwrite file to change mtime and contents
    df2 = pd.DataFrame({"id": ["1"], "name": ["B"]}).set_index("id")
    df2.to_pickle(p)
    b = load_pickle_df(str(p))
    # lru_cache keyed by mtime should reload to new contents
    assert b.equals(df2)

    clear_cache()
