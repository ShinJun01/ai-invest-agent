"""데이터 수집기 — us_v2 ARCHITECTURE.md 부록 A 작업 1 / 17장 Phase 1.

무료 소스(yfinance)로 레짐 엔진과 breadth 계산에 필요한 일봉을 모아
parquet 캐시(us_v2/data/store/)에 저장한다. 시점별(point-in-time) 유니버스가
아닌 현재 시점 S&P500 구성종목만 쓴다 — point-in-time은 Phase 2에서 유료
데이터로 교체(ARCHITECTURE.md §5-2).
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # us_v2/ -> `data` 패키지로 임포트
from data.store import MANIFEST_PATH, STORE_DIR, safe_name  # noqa: E402

CORE_TICKERS = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "GSPC": "^GSPC",   # S&P500 지수 자체 — V5 교차검증용 (FRED SP500과 대조)
    "VIX": "^VIX",
    "TNX": "^TNX",
    "DXY": "DX-Y.NYB",
    "RSP": "RSP",      # 동일가중 S&P500 — Breadth Pillar B (소수 대형주 착시 감지)
    "HYG": "HYG",       # 하이일드 회사채 — Risk Appetite Pillar D
    "IEF": "IEF",       # 7-10Y 국채 — HYG/IEF 신용 스프레드 대용
    "IWM": "IWM",       # 러셀2000 — 소형주 상대강도, Risk Appetite Pillar D
}

# VIX9D/VIX3M은 yfinance가 수 주씩 지연되는 경우가 잦아(V2 신선도 게이트가 실측으로 확인),
# ARCHITECTURE.md §17이 원래 지정한 CBOE 직접 소스를 쓴다.
CBOE_TICKERS = {
    "VIX9D": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv",
    "VIX3M": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv",
}

SECTOR_ETFS = {
    "XLK": "Info Tech", "XLF": "Financials", "XLE": "Energy", "XLV": "Health Care",
    "XLI": "Industrials", "XLY": "Cons Discretionary", "XLP": "Cons Staples",
    "XLU": "Utilities", "XLB": "Materials", "XLRE": "Real Estate", "XLC": "Comm Services",
}

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500_constituents() -> list[str]:
    """현재 시점 S&P500 구성종목 (Wikipedia). yfinance 표기(BRK.B -> BRK-B)로 정규화."""
    resp = requests.get(WIKI_SP500_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    table = pd.read_html(io.StringIO(resp.text))[0]
    return sorted(table["Symbol"].str.replace(".", "-", regex=False).tolist())


def download_cboe(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    df["DATE"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
    df = df.set_index("DATE").rename(columns=str.lower)
    df.index.name = "date"
    df["volume"] = 0  # 지수라 거래량 없음 — 스키마 일관성 위해 0으로 채움
    return df[["open", "high", "low", "close", "volume"]]


def download_batch(tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
    """yfinance 배치 다운로드. 실패/빈 종목은 결과 dict에서 제외(V6 커버리지가 감지)."""
    raw = yf.download(
        tickers, start=start, group_by="ticker", threads=True,
        auto_adjust=True, progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            df = raw[t] if len(tickers) > 1 else raw
        except KeyError:
            continue
        df = df.dropna(how="all")
        if df.empty:
            continue
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "date"
        out[t] = df[["Open", "High", "Low", "Close", "Volume"]].rename(columns=str.lower)
    return out


def save_all(data: dict[str, pd.DataFrame], source: str, manifest: dict) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat()
    for ticker, df in data.items():
        path = STORE_DIR / f"{safe_name(ticker)}.parquet"
        df.to_parquet(path)
        manifest[ticker] = {
            "source": source,
            "fetched_at": fetched_at,
            "as_of": df.index[-1].date().isoformat(),
            "rows": len(df),
            "path": path.name,
        }


def run(start: str = "2013-06-01") -> dict:
    """start 기본값: 2015-01-01부터 레짐 히스토리 라벨링(부록A 작업2/3)을 하려면
    252일 롤링(VIX 백분위 등) 워밍업 기간이 필요해 1.5년 여유를 둔다."""
    manifest: dict = {}

    print("[1/3] core + sector ETFs...")
    core_and_sector = {**CORE_TICKERS, **{k: k for k in SECTOR_ETFS}}
    raw = download_batch(list(core_and_sector.values()), start)
    # download_batch는 yfinance 심볼(예: ^VIX)로 반환 -> 내부에서 쓰는 이름(VIX)으로 리매핑
    core_data = {friendly: raw[yf_sym] for friendly, yf_sym in core_and_sector.items() if yf_sym in raw}
    save_all(core_data, source="yfinance", manifest=manifest)

    print("[1b/3] VIX9D/VIX3M (CBOE)...")
    cboe_data = {name: download_cboe(url) for name, url in CBOE_TICKERS.items()}
    save_all(cboe_data, source="cboe", manifest=manifest)

    print("[2/3] S&P500 constituents list...")
    constituents = fetch_sp500_constituents()
    (STORE_DIR / "sp500_constituents.json").write_text(
        json.dumps({"as_of": datetime.now(timezone.utc).date().isoformat(),
                     "tickers": constituents}, ensure_ascii=False, indent=2)
    )

    print(f"[3/3] {len(constituents)} constituents' daily bars (breadth 계산용)...")
    # yfinance 배치 한계 고려해 100종목씩 분할
    chunk = 100
    for i in range(0, len(constituents), chunk):
        batch = constituents[i:i + chunk]
        print(f"  {i}-{i+len(batch)} / {len(constituents)}")
        data = download_batch(batch, start)
        save_all(data, source="yfinance", manifest=manifest)

    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"done. {len(manifest)} tickers cached -> {STORE_DIR}")
    return manifest


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2013-06-01")
    args = parser.parse_args()
    run(start=args.start)
