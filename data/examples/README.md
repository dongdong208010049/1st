# CSV 적재 예시

공개 API가 없거나 계약이 필요한 자료는 CSV로 적재합니다. 아래 파일을 복사해
`data/` 아래에 두거나, 환경변수로 경로를 지정하세요.

| 파일 | 환경변수 | 쓰는 수집기 |
| --- | --- | --- |
| `internal_trade.csv` | `PARTNERS_INTERNAL_CSV` | `internal` (내부 구매·납기) |
| `credit_grades.csv` | `PARTNERS_CREDIT_CSV` | `credit` (신용평가 등급) |
| `risk_list.csv` | `PARTNERS_RISK_CSV` | `risk_list` (체불·산재·제재·부도·경매·해산·환경위반) |

`risk_list.csv`의 `kind`에 쓸 수 있는 값: 임금체불 / 산재 / 중대재해 / 제재 /
부정당업자 / 회생 / 파산 / 부도 / 경매 / 공매 / 체납 / 해산 / 조업정지 /
등기변동 / 환경위반. `severity`를 비우면 종류별 기본값이 들어갑니다.

`internal` 수집기는 ERP 연동(`PARTNERS_ERP_URL`, `PARTNERS_ERP_TOKEN`)이 있으면
CSV 대신 그쪽을 씁니다. 응답은 `{"rows": [ ... ]}` 형태로 CSV와 같은 필드를 담으면 됩니다.
