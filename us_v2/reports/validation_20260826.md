# 데이터 검증 결과 — 2026-08-26

전체 판정: PASS

| 게이트 | 대상 | 결과 | 메시지 |
|---|---|---|---|
| V1_schema | QQQ | PASS | ok |
| V2_freshness | QQQ | PASS | ok |
| V3_range | QQQ | PASS | ok |
| V4_continuity | QQQ | PASS | ok |
| V7_calendar | QQQ | PASS | ok |
| V1_schema | TNX | PASS | ok |
| V2_freshness | TNX | PASS | ok |
| V3_range | TNX | PASS | ok |
| V4_continuity | TNX | FAIL | |수익률|>30% 인 날 2건 (예: 2020-03-10, 2020-03-17) |
| V7_calendar | TNX | PASS | ok |
| V1_schema | SPY | PASS | ok |
| V2_freshness | SPY | PASS | ok |
| V3_range | SPY | PASS | ok |
| V4_continuity | SPY | PASS | ok |
| V7_calendar | SPY | PASS | ok |
| V1_schema | VIX3M | PASS | ok |
| V2_freshness | VIX3M | PASS | ok |
| V3_range | VIX3M | PASS | ok |
| V4_continuity | VIX3M | FAIL | |수익률|>30% 인 날 10건 (예: 2010-05-07, 2015-08-24, 2018-02-05, 2020-03-16, 2020-06-11) |
| V7_calendar | VIX3M | PASS | ok |
| V1_schema | VIX9D | PASS | ok |
| V2_freshness | VIX9D | PASS | ok |
| V3_range | VIX9D | PASS | ok |
| V4_continuity | VIX9D | FAIL | |수익률|>30% 인 날 132건 (예: 2011-01-28, 2011-02-22, 2011-08-04, 2011-08-08, 2011-08-09) |
| V7_calendar | VIX9D | PASS | ok |
| V1_schema | DXY | PASS | ok |
| V2_freshness | DXY | PASS | ok |
| V3_range | DXY | PASS | ok |
| V4_continuity | DXY | PASS | ok |
| V7_calendar | DXY | PASS | ok |
| V1_schema | RSP | PASS | ok |
| V2_freshness | RSP | PASS | ok |
| V3_range | RSP | PASS | ok |
| V4_continuity | RSP | PASS | ok |
| V7_calendar | RSP | PASS | ok |
| V1_schema | IEF | PASS | ok |
| V2_freshness | IEF | PASS | ok |
| V3_range | IEF | PASS | ok |
| V4_continuity | IEF | PASS | ok |
| V7_calendar | IEF | PASS | ok |
| V1_schema | HYG | PASS | ok |
| V2_freshness | HYG | PASS | ok |
| V3_range | HYG | PASS | ok |
| V4_continuity | HYG | PASS | ok |
| V7_calendar | HYG | PASS | ok |
| V1_schema | IWM | PASS | ok |
| V2_freshness | IWM | PASS | ok |
| V3_range | IWM | PASS | ok |
| V4_continuity | IWM | PASS | ok |
| V7_calendar | IWM | PASS | ok |
| V1_schema | GSPC | PASS | ok |
| V2_freshness | GSPC | PASS | ok |
| V3_range | GSPC | PASS | ok |
| V4_continuity | GSPC | PASS | ok |
| V7_calendar | GSPC | PASS | ok |
| V1_schema | VIX | PASS | ok |
| V2_freshness | VIX | PASS | ok |
| V3_range | VIX | PASS | ok |
| V4_continuity | VIX | FAIL | |수익률|>30% 인 날 30건 (예: 2014-01-24, 2014-07-17, 2015-06-29, 2015-08-21, 2015-08-24) |
| V7_calendar | VIX | PASS | ok |
| V5_cross_check | VIX | PASS | 2026-08-24 yfinance=15.85 fred=15.85 편차=0.000% |
| V5_cross_check | GSPC | PASS | 2026-08-25 yfinance=7677.28 fred=7677.28 편차=0.000% |
| V6_coverage | sp500_universe | PASS | 503/503 (100.0%) |
| V_constituents_defects | sp500_universe | PASS | 0/503 종목에서 스키마/범위 결함 |
| V4_continuity_flags | sp500_universe | PASS | 65개 종목 수동 확인 플래그 (실패 아님, 참고용): ALGN, AMD, APA, APP, APTV, BIIB, BLDR, CCL, CNC, COIN, CVNA, DDOG, DELL, DG, DHR, DRI, DVN, DXCM, ECHO, EOG, EQT, EW, FANG, FERG, FICO, FISV, FLEX, FRT, GDDY, GEN, GL, HAL, HONA, HOOD, HST, INCY, KIM, LITE, MGM, MNST, MRNA, MRVL, NCLH, NFLX, OKE, ORCL, OXY, PCG, PLTR, PSKY, RCL, RDDT, REG, SMCI, SNPS, TKO, TRGP, TTD, UAL, UBER, VLO, VRT, VRTX, WMB, WST |