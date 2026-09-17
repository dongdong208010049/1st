# HWP → PDF 변환기

한글(HWP/HWPX) 파일을 웹 브라우저에서 업로드하면 자동으로 PDF로 변환해주는 간단한 Flask 웹 애플리케이션입니다. 변환은 LibreOffice의 헤드리스(headless) 모드를 이용합니다.

같은 앱에 **협력사 종합 모니터링 대시보드**(`/partners`)가 포함되어 있습니다. 아래 [협력사 종합 모니터링 대시보드](#협력사-종합-모니터링-대시보드-partners) 절을 참고하세요.

## 동작 방식

1. 사용자가 웹 페이지에서 `.hwp` 또는 `.hwpx` 파일을 업로드합니다.
2. 서버가 파일을 임시 디렉터리에 저장하고 `soffice --headless --convert-to pdf`를 실행해 PDF로 변환합니다.
3. 변환된 PDF를 바로 다운로드로 응답합니다. 서버에 파일이 남지 않도록 임시 디렉터리는 요청이 끝나면 삭제됩니다.

## 요구 사항

- Python 3.10 이상
- LibreOffice (`soffice` 명령을 사용할 수 있어야 합니다)
- (선택) 한글이 포함된 문서를 올바르게 렌더링하려면 한글 폰트(`fonts-nanum`, `fonts-noto-cjk` 등)가 필요합니다.

## 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Ubuntu/Debian 기준 LibreOffice 및 한글 폰트 설치
sudo apt-get install -y libreoffice-writer fonts-nanum fonts-noto-cjk

python app.py
```

브라우저에서 `http://localhost:5000` 에 접속해 파일을 업로드합니다.

## Windows 개인 노트북에서 실행 (집/개인 LAN 전용 서버로 사용)

회사 문서를 제3자 온라인 변환 사이트에 올리지 않고, 개인 노트북을 상시 켜진 변환
서버로 써서 집 LAN 안에서만 접속하고 싶을 때의 절차입니다.

1. **Python 설치**: [python.org](https://www.python.org/downloads/)에서 3.10 이상 버전을
   내려받아 설치합니다. 설치 화면에서 **"Add python.exe to PATH"** 체크박스를 꼭
   선택하세요.
2. **LibreOffice 설치**: [libreoffice.org](https://www.libreoffice.org/download/download/)에서
   Windows용 설치 파일을 내려받아 기본 옵션으로 설치합니다 (Writer가 기본 포함됩니다).
   `converter.py`는 LibreOffice가 PATH에 없어도 기본 설치 경로
   (`C:\Program Files\LibreOffice\program\soffice.exe`)를 자동으로 찾습니다.
3. **소스 코드 받기**: 이 저장소를 내려받습니다 (Git이 있다면
   `git clone https://github.com/dongdong208010049/1st.git`, 없다면 GitHub 저장소
   페이지의 "Code → Download ZIP"으로 받아 압축을 풉니다).
4. **PowerShell에서 실행**:
   ```powershell
   cd 1st
   py -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```
5. 처음 실행 시 **Windows Defender 방화벽 알림**이 뜨면 **"개인 네트워크"만 체크**하고
   "공용 네트워크"는 체크 해제한 뒤 허용합니다. 이렇게 하면 집 LAN 안에서만 접근
   가능하고, 카페 와이파이 등 공용망에서는 접근할 수 없습니다.
6. 노트북 자신에서는 `http://localhost:5000`으로 접속합니다. 같은 집 LAN에 있는
   다른 기기(휴대폰 등)에서 쓰려면, `cmd`에서 `ipconfig`를 실행해 노트북의 IPv4
   주소(예: `192.168.0.15`)를 확인한 뒤 다른 기기 브라우저에서
   `http://192.168.0.15:5000`으로 접속합니다.

## Windows에서 설치 없이 실행 (포터블)

Python/LibreOffice의 설치 마법사를 실행하지 않고, 압축 풀기 + 더블클릭만으로 실행하고
싶다면 `portable/` 폴더의 스크립트를 사용하세요. 실제로는 "포터블(설치 프로그램 없는)"
버전의 Python과 LibreOffice를 이 폴더 안에 풀어서 쓰는 방식이라, 관리자 권한이나
레지스트리 변경 없이 동작합니다.

1. 이 저장소를 내려받아 압축을 풉니다 (GitHub의 "Code → Download ZIP").
2. `portable\setup.bat`을 더블클릭합니다. Python 임베더블 패키지와 pip를
   `portable\python-embed\`에 자동으로 내려받아 준비하고, 이 프로젝트의 의존성을
   설치합니다. (최초 1회만 인터넷 연결이 필요합니다.)
3. [portableapps.com/apps/office/libreoffice_portable](https://portableapps.com/apps/office/libreoffice_portable)에서
   LibreOffice Portable을 내려받아, 다음 경로에 `soffice.exe`가 있도록 `portable`
   폴더 안에 압축을 풉니다: `portable\LibreOfficePortable\App\libreoffice\program\soffice.exe`
4. `portable\run.bat`을 더블클릭하면 서버가 시작됩니다. 브라우저에서
   `http://localhost:5000`으로 접속합니다.

다른 기기에서도 쓰려면 위 "Windows 개인 노트북에서 실행" 절의 5~6단계(방화벽에서
"개인 네트워크"만 허용, `ipconfig`로 IP 확인)를 그대로 따르면 됩니다.

## 컨테이너로 실행 (Podman)

LibreOffice 설치 없이 바로 실행하고 싶다면 컨테이너 이미지를 사용할 수 있습니다.

> **Docker Desktop 대신 Podman을 권장합니다.** Docker Desktop은 직원 250명 이상 또는
> 연매출 1000만 달러 이상인 회사에서 사용 시 유료 구독이 필요합니다. Podman(및
> Podman Desktop)은 Apache 2.0 오픈소스로 회사 규모와 무관하게 무료이며, 명령어가
> Docker와 거의 동일해 아래 `Dockerfile`을 그대로 사용할 수 있습니다.

```bash
podman build -t hwp2pdf .
podman run --rm -p 5000:5000 hwp2pdf
```

Docker Engine(CLI, Desktop 아님)을 이미 쓰고 있다면 `podman`을 `docker`로 바꿔도 동일하게 동작합니다.

## 협력사 종합 모니터링 대시보드 (`/partners`)

같은 앱에 협력사(제조·구매 공급사)의 **경영 건강도**를 감시하는 대시보드가 함께 들어 있습니다.
담당자가 수치를 입력하는 방식이 아니라, 공공·신뢰 자료를 주기적으로 수집해 정규화·채점하고
협력사 리스트에 신호등으로 표시합니다.

### 화면

| 경로 | 내용 |
| --- | --- |
| `/partners/` | 전사 협력사 리스트. 종합 신호등·등급, 업종 배지, 카테고리별 신호등, 6개월 추세, 최종 갱신일. 상단에 업종별 집계 칩, 검색·업종·신호등·등급·담당자 필터와 정렬(위험 우선 기본) |
| `/partners/<id>` | 협력사 상세. 카테고리별 지표 값·점수·스파크라인, 즉시위험 사유, 자동 감점 내역, 리스크 이벤트 타임라인, 기본정보(업종 분류·담당자 등) 편집 |
| `/partners/settings` | **업종 분류 관리·협력사 등록**, 지표 정의(가중치·good/bad 기준·사용 여부) 편집, **지표 추가**, 수집기 상태, 수집 로그 |
| `/partners/api/partners.json` | 리스트와 같은 내용의 JSON (사내 다른 시스템 연동용) |

### 수집 소스

| 수집기 | 자료 | 채우는 지표 | 필요 키 |
| --- | --- | --- | --- |
| `dart` | DART 전자공시 재무제표 | 매출액, 매출 증감률, 영업이익률, 부채비율, 유동비율, 자본잠식률 | `DART_API_KEY` |
| `insurance` | 국민연금 사업장 가입 현황 | 가입자수, 3개월 인원 증감, 연금 체납 | `DATA_GO_KR_KEY` |
| `nts` | 국세청 사업자등록 상태 | 사업자상태(계속/휴업/폐업) | `DATA_GO_KR_KEY` |
| `risk_list` | 임금체불 명단·산재·행정제재 | 임금체불, 산재·중대재해, 행정제재 건수 + 이벤트 | CSV 적재 |

- **키가 없으면 목업 모드로 동작합니다.** 화면과 로그에 `목업`/`실 API`가 표시되므로 어떤 자료로 계산된 값인지 구분할 수 있습니다.
- 리스크 명단은 상시 개방 API가 일정하지 않아 CSV 적재를 1차 경로로 둡니다.
  `PARTNERS_RISK_CSV`(기본 `data/risk_list.csv`), 컬럼은 `biz_no,kind,title,occurred_on,severity,source,url`.
- DART는 상장·외부감사대상 법인만 공시 대상이라 소규모 협력사는 재무 지표가 비어 있을 수 있습니다.
  이 경우 해당 지표는 가중치에서 제외되고, 상세 화면의 '지표 충족률'로 신뢰도를 확인할 수 있습니다.
- 실 API 연동 코드는 각 기관의 공개 규격에 맞춰 작성되어 있으나, 이 저장소에는 키가 없어 응답 파싱까지 실제 호출로 검증하지는 않았습니다.
  키를 넣고 첫 수집 후 로그의 실패 메시지를 확인해 주세요.

### 종합등급 산정

1. 지표별 0~100점 = `good_value`(100점)~`bad_value`(0점) 선형 보간
2. 종합점수 = 값이 있는 지표의 **가중 평균** (자료가 없는 지표는 가중치에서 제외)
3. 자동 감점: 자료 노후(90일 초과) −5, 최근 1년 리스크 이벤트 건당 −3(최대 −15)
4. **즉시위험**에 하나라도 해당하면 점수와 무관하게 E·🔴 — 폐업, 휴업, 완전자본잠식,
   임금체불 명단 등재, 3개월 인원 30% 이상 급감
5. 등급: A(85+) · B(70+) · C(55+) · D(40+) · E. 신호등: 🟢70+ / 🟡40+ / 🔴40 미만

기준값·가중치는 `/partners/settings`에서 조정하고, 즉시위험 규칙은 `partners/scoring.py`의 `CRITICAL_RULES`에 있습니다.

### 업종(業) 분류

협력사는 **업종 분류**로 구분합니다. 기본값은 금형 · 양산처 · 지그 · 검사구 · 기타이며,
분류도 코드가 아니라 `partner_categories` 테이블의 데이터라서 계속 늘릴 수 있습니다.

- 추가·수정: `/partners/settings` → '업종 분류' (코드·표시명·정렬 순서·사용 여부)
- 코드로 기본값 지정: `partners/seed.py`의 `CATEGORIES`
- 추가하면 리스트 상단 집계 칩(`금형 2 🔴1`), 업종 필터, 협력사명 옆 배지에 바로 반영됩니다
- 협력사에 분류를 지정하는 곳: 상세 화면의 '기본정보' 또는 설정의 '협력사 등록'
- 사용을 끈 분류는 새로 선택할 수 없지만, 이미 지정된 협력사의 배지는 그대로 유지됩니다
- **지표와 가중치는 전사 공통**입니다. 업종별로 다른 지표를 보고 싶어지면 지표 정의에
  적용 분류를 두는 방식으로 확장할 수 있습니다(현재는 미구현).

### 항목 추가

지표는 코드가 아니라 `metric_defs` 테이블의 **정의**로 관리됩니다. 재무·인원·매출·경영환경 외에
항목을 늘릴 때 스키마를 바꿀 필요가 없습니다.

- 화면: `/partners/settings` → '지표 추가' (코드·표시명·카테고리·방향·good/bad·가중치)
- 코드: `partners/seed.py`의 `METRIC_DEFS`에 행 추가
- 추가한 지표는 리스트의 해당 카테고리 컬럼과 상세 화면에 자동으로 나타납니다. 값은 수집기 또는 `models.put_metric_value()`로 채웁니다.

### 실행

```bash
# 샘플 협력사 8곳 + 최근 12개월 목업 데이터로 바로 확인
python -c "from partners import models; from partners.seed import seed_all; seed_all(models.connect('instance/partners.db'))"
flask --app app run   # http://localhost:5000/partners/
```

데이터는 SQLite 한 파일(`instance/partners.db`, `PARTNERS_DB`로 변경 가능)에 저장됩니다.
기존 DB는 앱을 띄울 때 부족한 컬럼이 자동으로 추가되므로(`partners.category_code` 등) 따로 마이그레이션할 필요가 없습니다.
수집은 화면의 '지금 수집' 버튼 또는 `partners.collectors.run_collection()`으로 실행하며,
정기 수집은 cron·작업 스케줄러에서 같은 함수를 호출하면 됩니다.

## 테스트

변환 로직(`convert_to_pdf`)은 테스트에서 목(mock)으로 대체되므로, LibreOffice가 설치되어 있지 않은 환경에서도 테스트를 실행할 수 있습니다.

```bash
pip install -r requirements-dev.txt
pytest
```

## 제한 사항

- LibreOffice의 HWP 가져오기 필터를 사용하므로, 복잡한 표/그림/개체가 많은 문서는 원본 한글 프로그램과 서식이 다르게 보일 수 있습니다.
- 업로드 파일 크기는 기본 20MB로 제한되어 있습니다 (`app.py`의 `MAX_CONTENT_LENGTH`에서 조정 가능).
- 변환 시간은 최대 120초로 제한되어 있습니다 (`converter.py`의 `CONVERT_TIMEOUT_SECONDS`).
