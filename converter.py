"""LibreOffice 기반 HWP/HWPX -> PDF 변환 로직."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONVERT_TIMEOUT_SECONDS = 120

# Windows 설치본은 기본적으로 PATH에 등록되지 않으므로 기본 설치 경로도 확인한다.
_WINDOWS_FALLBACK_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


class ConversionError(RuntimeError):
    """HWP/HWPX 파일을 PDF로 변환할 수 없을 때 발생한다."""


def _find_soffice() -> str | None:
    # 포터블(설치 없는) 배포본처럼 임의 경로에 있는 soffice를 쓰고 싶을 때의 탈출구.
    env_override = os.environ.get("SOFFICE_PATH")
    if env_override and Path(env_override).exists():
        return env_override

    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found

    if sys.platform == "win32":
        for candidate in _WINDOWS_FALLBACK_PATHS:
            if Path(candidate).exists():
                return candidate
    return None


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
        profile_uri = Path(profile_dir).as_uri()
        cmd = [
            soffice_bin,
            f"-env:UserInstallation={profile_uri}",
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
