# Install claude-session-mover on Windows. Uses uv or pipx when present; otherwise puts the single-file
# script and a .cmd launcher in %LOCALAPPDATA%\claude-session-mover\bin and adds that folder to your PATH.
#   irm https://raw.githubusercontent.com/Marzipane/claude-session-mover/main/install.ps1 | iex
# Also install the agent that keeps every Claude Desktop account's chats in step:
#   $env:CSM_AUTO_SYNC = 1; irm https://raw.githubusercontent.com/Marzipane/claude-session-mover/main/install.ps1 | iex
param([switch]$AutoSync)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Repo = 'https://github.com/Marzipane/claude-session-mover'
$Ref = if ($env:CSM_REF) { $env:CSM_REF } else { 'main' }
$Raw = "https://raw.githubusercontent.com/Marzipane/claude-session-mover/$Ref/claude_session_mover.py"
$WantsAgent = $AutoSync -or ($env:CSM_AUTO_SYNC -eq '1')

function Find-Python {
    # The 'python' that Windows ships without Python installed is a Microsoft Store stub that runs nothing,
    # so each candidate has to prove it can run code and is new enough.
    foreach ($candidate in @(@('py', '-3'), @('python'), @('python3'))) {
        if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
        $exe = $candidate[0]
        $prefix = @($candidate | Select-Object -Skip 1)
        $found = & $exe @prefix -c 'import sys; print(sys.executable) if sys.version_info >= (3, 8) else None' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { return "$found".Trim() }
    }
    return $null
}

function Invoke-Checked([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

$python = Find-Python
if (-not $python) {
    throw 'claude-session-mover needs Python 3.8 or newer. Install it from https://www.python.org/downloads/ or with "winget install Python.Python.3.12", then run this again.'
}

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Invoke-Checked 'uv tool install' { uv tool install --force "git+$Repo@$Ref" }
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
    Invoke-Checked 'pipx install' { pipx install --force "git+$Repo@$Ref" }
} else {
    $bin = Join-Path $env:LOCALAPPDATA 'claude-session-mover\bin'
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    $script = Join-Path $bin 'claude_session_mover.py'
    $download = "$script.download"
    Invoke-WebRequest -UseBasicParsing -Uri $Raw -OutFile $download -TimeoutSec 60
    Invoke-Checked 'checking the download' { & $python -c 'import ast, sys; ast.parse(open(sys.argv[1], ''rb'').read())' $download }
    Move-Item -Force $download $script
    Set-Content -Encoding ASCII -Path (Join-Path $bin 'claude-session-mover.cmd') -Value "@`"$python`" `"%~dp0claude_session_mover.py`" %*"
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not (($userPath -split ';') -contains $bin)) {
        [Environment]::SetEnvironmentVariable('Path', (@($userPath, $bin) | Where-Object { $_ }) -join ';', 'User')
        Write-Host "Added $bin to your PATH (open a new terminal to use it everywhere)."
    }
    if (-not (($env:Path -split ';') -contains $bin)) { $env:Path = "$env:Path;$bin" }
}

$tool = Get-Command claude-session-mover -ErrorAction SilentlyContinue
if (-not $tool) {
    $userBin = Join-Path $HOME '.local\bin\claude-session-mover.exe'
    if (Test-Path $userBin) { $tool = Get-Item $userBin }
}
if (-not $tool) { throw 'Installed, but claude-session-mover is not on PATH; open a new terminal and run: claude-session-mover list' }
$toolPath = if ($tool.Source) { $tool.Source } else { $tool.FullName }

Write-Host 'Installed. Try: claude-session-mover list'
if ($WantsAgent) { Invoke-Checked 'install-agent' { & $toolPath install-agent } }
