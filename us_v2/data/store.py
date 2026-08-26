"""parquet 캐시 공용 접근자. ingest.py가 쓰고, validate.py/engines가 읽는다."""
from pathlib import Path

import pandas as pd

STORE_DIR = Path(__file__).parent / "store"
MANIFEST_PATH = STORE_DIR / "manifest.json"


def safe_name(ticker: str) -> str:
    return ticker.replace("^", "").replace(".", "-")


def load(ticker: str) -> pd.DataFrame:
    return pd.read_parquet(STORE_DIR / f"{safe_name(ticker)}.parquet")
