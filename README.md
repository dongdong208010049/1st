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

## Docker로 실행

LibreOffice 설치 없이 바로 실행하고 싶다면 Docker 이미지를 사용할 수 있습니다.

```bash
docker build -t hwp2pdf .
docker run --rm -p 5000:5000 hwp2pdf
```

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
