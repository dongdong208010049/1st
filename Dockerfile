FROM python:3.11-slim

# LibreOffice(Writer)와 한글 폰트를 설치해 HWP/HWPX 문서를 렌더링할 수 있게 한다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        fonts-nanum \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000
CMD ["python", "app.py"]
