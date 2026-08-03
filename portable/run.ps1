$root = $PSScriptRoot
$py = Join-Path $root "python-embed\python.exe"
$soffice = Join-Path $root "LibreOfficePortable\App\libreoffice\program\soffice.exe"
$appPy = Join-Path $root "..\app.py"

if (-not (Test-Path $py)) {
    Write-Host "Python이 준비되지 않았습니다. 먼저 setup.bat 을 실행하세요."
    exit 1
}

if (-not (Test-Path $soffice)) {
    Write-Host "LibreOffice Portable을 찾을 수 없습니다: $soffice"
    Write-Host "https://portableapps.com/apps/office/libreoffice_portable 에서 내려받아"
    Write-Host "위 경로에 soffice.exe가 있도록 압축을 풀어주세요."
    exit 1
}

$env:SOFFICE_PATH = $soffice

Write-Host "HWP -> PDF 변환 서버를 시작합니다. 종료하려면 이 창에서 Ctrl+C 를 누르세요."
& $py $appPy
