"""HWP/HWPX 파일을 PDF로 변환해주는 간단한 웹 서비스."""

import io
import os
import tempfile
import uuid
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, send_file, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

import partners
from converter import ConversionError, convert_to_pdf

ALLOWED_EXTENSIONS = {".hwp", ".hwpx"}
MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 20 MB

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# 협력사 종합 모니터링 대시보드(/partners)를 같은 앱에 붙인다.
partners.init_app(app)


def _is_allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    uploaded = request.files.get("file")

    if uploaded is None or uploaded.filename == "":
        flash("변환할 한글(HWP) 파일을 선택해 주세요.")
        return redirect(url_for("index"))

    if not _is_allowed(uploaded.filename):
        flash("HWP(.hwp) 또는 HWPX(.hwpx) 파일만 업로드할 수 있습니다.")
        return redirect(url_for("index"))

    original_ext = Path(uploaded.filename).suffix.lower()
    # 디스크에는 충돌/경로 조작 방지를 위해 무작위 이름을 사용하고,
    # 다운로드 파일명은 원본 이름에서 최대한 살려서 사용한다.
    disk_filename = f"{uuid.uuid4().hex}{original_ext}"
    download_stem = Path(secure_filename(uploaded.filename)).stem or "document"

    with tempfile.TemporaryDirectory(prefix="hwp2pdf-") as workdir:
        src_path = Path(workdir) / disk_filename
        uploaded.save(src_path)

        try:
            pdf_bytes = convert_to_pdf(src_path, Path(workdir))
        except ConversionError as exc:
            flash(f"변환에 실패했습니다: {exc}")
            return redirect(url_for("index"))

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{download_stem}.pdf",
    )


@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(_exc):
    flash("파일 크기가 너무 큽니다. 20MB 이하 파일만 업로드할 수 있습니다.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
