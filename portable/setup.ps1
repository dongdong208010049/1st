$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$pyDir = Join-Path $root "python-embed"
$pyVersion = "3.12.7"
$pyZipUrl = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip"
$getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
$sofficePath = Join-Path $root "LibreOfficePortable\App\libreoffice\program\soffice.exe"

if (-not (Test-Path (Join-Path $pyDir "python.exe"))) {
    Write-Host "Python $pyVersion 임베더블 패키지를 내려받는 중..."
    $zipPath = Join-Path $root "python-embed.zip"
    Invoke-WebRequest -Uri $pyZipUrl -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $pyDir -Force
    Remove-Item $zipPath

    Write-Host "site-packages(pip) 활성화 중..."
    $pthFile = Get-ChildItem -Path $pyDir -Filter "python3*._pth" | Select-Object -First 1
    (Get-Content $pthFile.FullName) -replace '^#\s*import site', 'import site' | Set-Content $pthFile.FullName

    Write-Host "pip 설치 중..."
    $getPipPath = Join-Path $pyDir "get-pip.py"
    Invoke-WebRequest -Uri $getPipUrl -OutFile $getPipPath
    & (Join-Path $pyDir "python.exe") $getPipPath --no-warn-script-location
    Remove-Item $getPipPath
} else {
    Write-Host "Python 런타임이 이미 준비되어 있습니다."
}

Write-Host "앱 의존성 설치 중..."
& (Join-Path $pyDir "python.exe") -m pip install --no-warn-script-location -r (Join-Path $root "..\requirements.txt")

Write-Host ""
Write-Host "===================================================="
Write-Host "설정 완료."
if (-not (Test-Path $sofficePath)) {
    Write-Host ""
    Write-Host "다음 단계가 남았습니다:"
    Write-Host "  1. https://portableapps.com/apps/office/libreoffice_portable 에서"
    Write-Host "     LibreOffice Portable을 내려받습니다."
    Write-Host "  2. 실행해서 압축을 풀 때, 아래 경로에 soffice.exe가 생기도록"
    Write-Host "     이 폴더를 대상으로 지정합니다:"
    Write-Host "       $root"
    Write-Host "     (즉 최종적으로 $sofficePath 가 존재해야 합니다)"
    Write-Host "  3. 완료되면 run.bat 을 실행하세요."
} else {
    Write-Host "LibreOffice Portable도 이미 준비되어 있습니다. run.bat 을 실행하세요."
}
Write-Host "===================================================="
