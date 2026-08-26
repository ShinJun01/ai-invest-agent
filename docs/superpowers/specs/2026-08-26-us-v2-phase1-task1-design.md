# US V2 Phase 1 — 작업 1: 데이터 수집기 + 검증 게이트

**기준 문서:** `us_v2/ARCHITECTURE.md` (부록 A 작업 1, 14-2, 15-3). 이 문서는
그 설계를 복제하지 않고, 이번 세션에서 구체화한 결정만 기록한다.

## 저장소 구조
- `kr/` — 기존 한국장 워크스페이스, 무변경 이동
- `us_v2/` — 아키텍처 문서 §15-3 레이아웃(config/data/engines/backtest/brief/reports)
- 저장소: `ShinJun01/ai-invest-agent` (private, 단일 repo, 폴더로 KR/US 분리)

## 이번 작업 범위
`us_v2/data/ingest.py`, `us_v2/data/validate.py` — 부록 A의 "작업 1"만. 레짐 엔진(작업 2)과
유효성 검증(작업 3)은 다음 세션.

## 데이터 소스 결정 (구현 중 실측으로 변경된 부분 포함)
| 대상 | 소스 | 비고 |
|---|---|---|
| SPY/QQQ/섹터ETF 11종/VIX/TNX/DXY/GSPC | yfinance | 무료, Phase 1 승인 소스(§17) |
| **VIX9D/VIX3M** | **CBOE 직접 CSV** (`cdn.cboe.com/.../VIX9D_History.csv` 등) | 당초 yfinance로 시도했으나 V2 신선도 게이트가 실측으로 39일 지연을 잡아냄 → §17이 원래 지정한 CBOE 소스로 교체. 결과적으로 스펙대로 |
| S&P500 구성종목 리스트 | Wikipedia 스크랩 (requests + User-Agent 헤더) | pandas.read_html 단독 호출은 403(UA 없음)이라 requests로 우회. Phase 1은 현재 시점 구성만 필요(시점별 구성은 Phase 2, §5-2) |
| S&P500 구성종목 일봉 | yfinance 배치 다운로드(100종목씩 분할) | breadth(%above50DMA, NH-NL) 계산용 |
| SPY/VIX 2차 소스(V5 교차검증) | **FRED CSV** (`fredgraph.csv?id=VIXCLS`, `id=SP500`) | stooq는 봇 차단(JS PoW 챌린지)으로 접근 불가 → FRED로 교체. SPY 대신 ^GSPC(지수 자체)로 대조 — SPY는 ETF라 지수값과 직접 비교 불가 |

## V1~V7 게이트 구현 방식
- V1 스키마 / V3 범위 / V6 커버리지(≥98%): 문서 그대로 구현
- V2 신선도: 최신 봉의 날짜가 오늘 기준 직전 거래일과 일치하는지
- **V4 연속성(|r|>30%): 실패해도 HALT를 막지 않는 비차단 플래그로 구현.** 문서 원문("분할/오류
  의심 → 수동 확인 플래그")대로. 500종목×3년 표본에서 실적 발표 등으로 정상적인 30%+ 단일
  종목 변동이 다수 발생하고(실측 26/503종목), VIX류 지수도 스파이크가 흔해 하드 실패로
  두면 정상 데이터로 매번 HALT됨
- V5 교차검증: VIX·GSPC 종가를 FRED와 대조, 편차 >0.1%면 실패
- V7 캘린더: 별도 공휴일 라이브러리 없이 SPY 데이터 자체의 거래일 시퀀스를 캘린더로 사용
  (실제 데이터에 없는 날 = 휴장일). 새 의존성 추가하지 않기 위한 단순화.
  → ponytail: 지수 리밸런싱/반장(half-day) 구분은 못 함, 필요해지면
  `pandas_market_calendars`로 교체

하나라도 실패 시 해당 종목/지표를 스킵하고 `reports/validation_YYYYMMDD.md`에 사유 기록
(문서상 "HALT"는 전체 시스템 중단이지만, Phase 1은 매매가 없으므로 브리핑 생성 차단으로
해석 — 작업 3 이후 확정).

## 산출물
- `us_v2/data/store/*.parquet` (종목별 캐시)
- `us_v2/data/ingest.py`, `us_v2/data/validate.py`
- `us_v2/test_validate.py` — 합성 데이터로 V1~V7 각 게이트 pass/fail 케이스 assert
- `us_v2/requirements.txt`
