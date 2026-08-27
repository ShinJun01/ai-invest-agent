# AI 투자 에이전트

Claude 기반 AI 투자·매매 시스템. 두 개의 독립된 하위 시스템으로 구성된다.

## kr/ — 한국장 (운영 중)
《클로드코워크 AI 주식 자동매매》(김우민, 로이북스) 기반. 매일 07:00 KST 브리핑을
생성하는 마크다운/텍스트 워크스페이스. 구조는 `kr/가이드.txt`, 진행 상황은
`kr/00_전체정리.md` 참고.

## us_v2/ — 미국장 V2 (구현 중)
한국장 시스템을 미국시장(NASDAQ/NYSE)에 맞게 전면 재설계한 퀀트 트레이딩 시스템.
설계 근거와 전체 아키텍처는 `us_v2/ARCHITECTURE.md`(단일 소스) 참고.

- 레짐 게이팅 + 횡단면 종목 선정 + 변동성 기반 리스크 관리 3축 구조
- 코드가 판단, AI는 해석·검증만 담당 (숫자 생성 금지, Numeric Guard로 검증)
- Phase 1(데이터+레짐+브리핑) → **Phase 2(스크리닝+시그널+사이징, 진행 중)** →
  Phase 3(리스크+페이퍼) → Phase 4(실자본)

### 현재 상태 (구현 순서대로)

| 파일 | 역할 | 상태 |
|---|---|---|
| `data/ingest.py` | 일봉 수집(yfinance+CBOE), S&P500 구성종목/섹터 | 완료 |
| `data/validate.py` | V1~V7 데이터 검증 게이트 | 완료 |
| `engines/regime.py` | 4-Pillar 레짐 판정(§3), 2일 확인규칙 | 완료 |
| `engines/regime_validate.py` | 레짐 유효성 검증(부록A 작업3) | **FAIL** — 결과는 `reports/regime_validation.md` 참고. 폭락 후 반등 현상으로 추정, 재가중 없이 보류 중 |
| `engines/screen.py` | 종목 스크리닝(§5, Stage 0~5) | 완료 (유니버스는 S&P500만, NASDAQ100/MidCap400 미확장) |
| `engines/signals.py` | S1 Trend Pullback 진입/손절 확정(§9-1) | 완료 |
| `engines/sizing.py` | RAER + 리스크 상한 기반 포지션 사이징(§8/§10) | 완료 |
| `backtest/engine.py`, `backtest/report.py` | S1 히스토리 백테스트(§11) + Tier1/2 성과 | **MVP** — 생존편향 있음(시점별 유니버스 아님), screen.py Stage5 선별/sizing.py 리스크캡 미적용 → 개별 트레이드 기대값은 거의 0인데 포트폴리오 MDD -88%. `reports/backtest_S1_report.md` 참고 |
| `backtest/validators.py` (과최적화 방어 10종, §11-4), S2~S5 전략 | — | 미착수 |

매일 실행 순서: `data/ingest.py` → `data/validate.py` → `engines/regime.py` →
`engines/screen.py` → `engines/signals.py` → `engines/sizing.py --equity <계좌액>`.
각 단계 산출물은 `us_v2/reports/`에 날짜별로 쌓인다. 자체검증은 `us_v2/test_*.py`
(프레임워크 없는 assert 스크립트, 전부 통과 상태).

진행 결정/스코프 기록은 `docs/superpowers/specs/`.

**실계좌 매매 없음.** Phase 4 이전까지 전량 모의/페이퍼 트레이딩.
