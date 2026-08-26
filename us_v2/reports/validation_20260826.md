# 데이터 검증 결과 — 2026-08-26

전체 판정: PASS

| 게이트 | 대상 | 결과 | 메시지 |
|---|---|---|---|
| V1_schema | VIX3M | PASS | ok |
| V2_freshness | VIX3M | PASS | ok |
| V3_range | VIX3M | PASS | ok |
| V4_continuity | VIX3M | FAIL | |수익률|>30% 인 날 10건 (예: 2010-05-07, 2015-08-24, 2018-02-05, 2020-03-16, 2020-06-11) |
| V7_calendar | VIX3M | PASS | ok |
| V1_schema | GSPC | PASS | ok |
| V2_freshness | GSPC | PASS | ok |
| V3_range | GSPC | PASS | ok |
| V4_continuity | GSPC | PASS | ok |
| V7_calendar | GSPC | PASS | ok |
| V1_schema | QQQ | PASS | ok |
| V2_freshness | QQQ | PASS | ok |
| V3_range | QQQ | PASS | ok |
| V4_continuity | QQQ | PASS | ok |
| V7_calendar | QQQ | PASS | ok |
| V1_schema | VIX9D | PASS | ok |
| V2_freshness | VIX9D | PASS | ok |
| V3_range | VIX9D | PASS | ok |
| V4_continuity | VIX9D | FAIL | |수익률|>30% 인 날 132건 (예: 2011-01-28, 2011-02-22, 2011-08-04, 2011-08-08, 2011-08-09) |
| V7_calendar | VIX9D | PASS | ok |
| V1_schema | VIX | PASS | ok |
| V2_freshness | VIX | PASS | ok |
| V3_range | VIX | PASS | ok |
| V4_continuity | VIX | FAIL | |수익률|>30% 인 날 8건 (예: 2024-08-05, 2024-09-03, 2024-12-18, 2025-04-03, 2025-04-04) |
| V7_calendar | VIX | PASS | ok |
| V1_schema | TNX | PASS | ok |
| V2_freshness | TNX | PASS | ok |
| V3_range | TNX | PASS | ok |
| V4_continuity | TNX | PASS | ok |
| V7_calendar | TNX | PASS | ok |
| V1_schema | DXY | PASS | ok |
| V2_freshness | DXY | PASS | ok |
| V3_range | DXY | PASS | ok |
| V4_continuity | DXY | PASS | ok |
| V7_calendar | DXY | PASS | ok |
| V1_schema | SPY | PASS | ok |
| V2_freshness | SPY | PASS | ok |
| V3_range | SPY | PASS | ok |
| V4_continuity | SPY | PASS | ok |
| V7_calendar | SPY | PASS | ok |
| V5_cross_check | VIX | PASS | 2026-08-24 yfinance=15.85 fred=15.85 편차=0.000% |
| V5_cross_check | GSPC | PASS | 2026-08-25 yfinance=7677.28 fred=7677.28 편차=0.000% |
| V6_coverage | sp500_universe | PASS | 502/503 (99.8%) |
| V_constituents_defects | sp500_universe | PASS | 1/503 종목에서 스키마/범위 결함 |
| V4_continuity_flags | sp500_universe | PASS | 26개 종목 수동 확인 플래그 (실패 아님, 참고용): ALGN, APP, CNC, COIN, CVNA, DDOG, DELL, DG, DXCM, ECHO, EW, FISV, FLEX, GL, HONA, MNST, MRNA, MRVL, ORCL, PLTR, PSKY, RDDT, SMCI, SNPS, TTD, WST |