# HWP → PDF 변환기

한글(HWP/HWPX) 파일을 웹 브라우저에서 업로드하면 자동으로 PDF로 변환해주는 간단한 Flask 웹 애플리케이션입니다. 변환은 LibreOffice의 헤드리스(headless) 모드를 이용합니다.

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
