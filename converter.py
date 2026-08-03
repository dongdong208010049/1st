"""LibreOffice 기반 HWP/HWPX -> PDF 변환 로직."""

import shutil
import subprocess
import tempfile
from pathlib import Path

CONVERT_TIMEOUT_SECONDS = 120


class ConversionError(RuntimeError):
    """HWP/HWPX 파일을 PDF로 변환할 수 없을 때 발생한다."""


def _find_soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def convert_to_pdf(src_path: Path, workdir: Path) -> bytes:
    """src_path의 문서를 PDF로 변환하고 결과 바이트를 반환한다.

    workdir는 src_path가 위치한 임시 디렉터리여야 하며, 변환된 PDF도
    같은 디렉터리에 생성된다.
    """
    soffice_bin = _find_soffice()
    if soffice_bin is None:
        raise ConversionError("LibreOffice(soffice)가 설치되어 있지 않습니다.")

    # 동시 요청 시 LibreOffice 사용자 프로필이 충돌하지 않도록 매 변환마다
    # 격리된 프로필 디렉터리를 사용한다.
    with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile_dir:
        cmd = [
            soffice_bin,
            f"-env:UserInstallation=file://{profile_dir}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(workdir),
            str(src_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=CONVERT_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("변환 시간이 초과되었습니다.") from exc

    pdf_path = src_path.with_suffix(".pdf")
    if result.returncode != 0 or not pdf_path.exists():
        stderr = result.stderr.decode("utf-8", errors="ignore").strip()
        raise ConversionError(stderr or "알 수 없는 오류로 변환에 실패했습니다.")

    return pdf_path.read_bytes()
