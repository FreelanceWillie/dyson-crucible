# prereqs.ps1 - self-assemble machine prerequisites (Python 3.10+, Git).
# Dot-sourced by bootstrap.ps1 AND setup.ps1 so BOTH the install path and the
# update path can install/upgrade what is missing. Self-contained (uses Write-Host;
# does not depend on the caller's helpers).

# Re-read PATH from the registry so tools installed in THIS session are found
# without opening a new terminal.
function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ";"
}

# Return the invocation for a real Python 3.10+ ("py -3", "python", ...) or $null.
# Rejects the Microsoft Store stub and any Python older than 3.10 (e.g. 3.9).
function Get-Python310 {
    foreach ($cand in @(@("py", @("-3")), @("python", @()), @("python3", @()))) {
        try {
            $v = (& $cand[0] @($cand[1]) --version) 2>&1
            if ($LASTEXITCODE -eq 0 -and "$v" -match "Python (\d+)\.(\d+)") {
                if ((([int]$Matches[1] -eq 3) -and ([int]$Matches[2] -ge 10)) -or ([int]$Matches[1] -gt 3)) {
                    return @{ Exe = $cand[0]; Args = $cand[1]; Version = "$v" }
                }
            }
        } catch {}
    }
    return $null
}

# Ensure Python 3.10+ exists (installs it if missing OR too old, e.g. 3.9).
# Returns a hashtable @{Exe; Args; Version} or $null if it could not be provided.
function Ensure-Python {
    $p = Get-Python310
    if ($p) { Write-Host "      Python OK: $($p.Version)" -ForegroundColor Green; return $p }

    Write-Host "      No Python 3.10+ found (you may have an older one). Installing 3.12..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try { winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements --silent } catch {}
        Refresh-Path
        $p = Get-Python310
        if ($p) { Write-Host "      Python installed (winget): $($p.Version)" -ForegroundColor Green; return $p }
    }
    # Fallback: download + silent-install the official python.org build.
    $ver = "3.12.7"
    $exe = Join-Path $env:TEMP "python-$ver-amd64.exe"
    try {
        Write-Host "      Downloading the Python installer..."
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest "https://www.python.org/ftp/python/$ver/python-$ver-amd64.exe" -OutFile $exe -UseBasicParsing -ErrorAction Stop
        Write-Host "      Installing Python (silent, adds it to PATH)..."
        Start-Process $exe -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1" -Wait
        Refresh-Path
    } catch { Write-Host "      Python auto-install hit a problem: $_" -ForegroundColor DarkYellow }
    $p = Get-Python310
    if ($p) { Write-Host "      Python installed: $($p.Version)" -ForegroundColor Green; return $p }

    Write-Host ""
    Write-Host "  Could not auto-install Python 3.10+." -ForegroundColor Red
    Write-Host "  Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')," -ForegroundColor Red
    Write-Host "  then double-click the launcher again (it resumes)." -ForegroundColor Red
    return $null
}

# Ensure Git (needed for ComfyUI custom nodes / feature packs). Best-effort.
function Ensure-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) { return $true }
    Write-Host "      Git not found. Installing it..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try { winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements --silent } catch {}
        Refresh-Path
    }
    if (Get-Command git -ErrorAction SilentlyContinue) { Write-Host "      Git installed." -ForegroundColor Green; return $true }
    Write-Host "      Could not auto-install Git. The app runs, but feature-pack nodes need it." -ForegroundColor DarkYellow
    return $false
}
