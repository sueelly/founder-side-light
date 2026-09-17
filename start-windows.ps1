param([switch]$OpenWindow)
$ErrorActionPreference = 'Stop'

# Open an interactive terminal for the participant.
if ($OpenWindow) {
    $founderScript = $PSCommandPath.Replace("'", "''")
    $founderLaunch = "Remove-Item Env:CLAUDECODE -ErrorAction SilentlyContinue; & '$founderScript'"
    $founderEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($founderLaunch))
    Start-Process powershell.exe -ArgumentList @('-NoProfile', '-NoExit', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $founderEncoded)
    exit
}
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
Set-Location -LiteralPath $PSScriptRoot
Write-Host '경량 면접 준비' -ForegroundColor Cyan
Write-Host '이 창이 설치와 점검을 진행하고 필요한 정보만 물어봅니다. 명령을 입력할 필요가 없습니다.'
if ($env:OS -ne 'Windows_NT') { throw 'This launcher requires Windows.' }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:CLAUDECODE -ErrorAction SilentlyContinue

# Smart App Control can reject uv's unsigned managed CPython. Prefer an
# installed system Python, which is signed and already trusted by Windows.
function Find-FounderPython {
    $founderPy = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($founderPy) {
        foreach ($founderVersion in @('3.13', '3.12', '3.11', '3')) {
            try {
                & $founderPy.Source "-$founderVersion" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { return @($founderPy.Source, "-$founderVersion") }
            } catch { }
        }
    }
    $founderCandidates = @()
    $founderCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($founderCommand -and $founderCommand.Source -notlike '*\WindowsApps\*') {
        $founderCandidates += $founderCommand.Source
    }
    # WinGet may finish without refreshing PATH in this process.
    foreach ($founderVersion in @('313', '312', '311')) {
        if ($env:LOCALAPPDATA) { $founderCandidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python$founderVersion\python.exe" }
        if ($env:ProgramFiles) { $founderCandidates += Join-Path $env:ProgramFiles "Python$founderVersion\python.exe" }
    }
    foreach ($founderCandidate in $founderCandidates) {
        if (Test-Path -LiteralPath $founderCandidate) {
            try {
                & $founderCandidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { return @($founderCandidate) }
            } catch { }
        }
    }
}

$founderPython = @(Find-FounderPython)
if (-not $founderPython.Count) {
    $founderWinget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($founderWinget) {
        Write-Host 'Python을 자동 설치합니다. 잠시 기다려 주세요.'
        New-Item -ItemType Directory -Force -Path '.bootstrap' | Out-Null
        $founderPythonLog = Join-Path $PSScriptRoot '.bootstrap\python-install.log'
        $founderPythonErr = Join-Path $PSScriptRoot '.bootstrap\python-install-error.log'
        $founderInstall = Start-Process -FilePath $founderWinget.Source -ArgumentList @(
            'install', '--id', 'Python.Python.3.13', '--exact', '--source', 'winget', '--scope', 'user',
            '--silent', '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity'
        ) -RedirectStandardOutput $founderPythonLog -RedirectStandardError $founderPythonErr -Wait -PassThru
        if ($founderInstall.ExitCode -ne 0) {
            throw "Python 자동 설치에 실패했습니다. $founderPythonLog 및 $founderPythonErr 를 확인하세요."
        }
        $founderPython = @(Find-FounderPython)
    }
    if (-not $founderPython.Count) {
        Start-Process 'https://www.python.org/downloads/windows/'
        throw 'Python 자동 설치를 완료하지 못했습니다. 열린 공식 페이지에서 Python 3.13을 설치한 뒤 실행.bat를 다시 여세요.'
    }
}

if ($founderPython.Count -eq 2) {
    & $founderPython[0] $founderPython[1] (Join-Path $PSScriptRoot 'bootstrap.py')
} else {
    & $founderPython[0] (Join-Path $PSScriptRoot 'bootstrap.py')
}
if ($LASTEXITCODE -ne 0) { throw '준비 또는 면접이 중단됐습니다. 위 안내를 확인한 뒤 같은 폴더에서 다시 시작하세요.' }
