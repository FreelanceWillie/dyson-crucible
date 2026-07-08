# verify_comfyui.ps1 - self-test + self-heal the ComfyUI engine.
# Dot-sourced by bootstrap.ps1 (and runnable standalone). Ensures ComfyUI is
# installed, LAUNCHABLE (any build type), wired into config.yaml, and actually
# responds -- so "gen failed / could not reach ComfyUI" heals itself.

function Find-ComfyMain($root) {
    foreach ($c in @((Join-Path $root "ComfyUI\main.py"), (Join-Path $root "main.py"))) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

function Find-ComfyPython($root, $repoRoot) {
    # Portable build ships its own python; otherwise use the repo venv python.
    foreach ($c in @((Join-Path $root "python_embeded\python.exe"),
                     (Join-Path (Split-Path $root -Parent) "python_embeded\python.exe"))) {
        if (Test-Path $c) { return $c }
    }
    $venv = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    return $null
}

# Return a launcher (.bat) that starts ComfyUI, generating one for source builds.
function Ensure-ComfyLauncher($root, $repoRoot) {
    $mainPy = Find-ComfyMain $root
    if (-not $mainPy) { return $null }
    # Portable build ships run_nvidia_gpu.bat -- prefer it.
    foreach ($c in @((Join-Path $root "run_nvidia_gpu.bat"),
                     (Join-Path $root "ComfyUI\run_nvidia_gpu.bat"))) {
        if (Test-Path $c) { return $c }
    }
    # Source (git-clone) build: install ComfyUI's own deps into the python we will
    # launch it with, then write a small launcher.
    $py = Find-ComfyPython $root $repoRoot
    if (-not $py) { return $null }
    $comfyDir = Split-Path $mainPy -Parent
    $reqs = Join-Path $comfyDir "requirements.txt"
    if (Test-Path $reqs) {
        Write-Host "      Installing ComfyUI's dependencies (source build)..." -ForegroundColor Yellow
        # Install ComfyUI's reqs but NOT torch/torchvision/torchaudio -- the venv
        # already has a matched CUDA torch. Letting ComfyUI's reqs pull torch* causes
        # a fatal torchaudio/torch DLL mismatch (torch_library_impl / _torchaudio.pyd).
        $filtered = Join-Path $env:TEMP "comfy_reqs_no_torch.txt"
        Get-Content $reqs | Where-Object { $_ -notmatch '^\s*(torch|torchvision|torchaudio)([<>=!~ ].*)?$' } | Set-Content $filtered -Encoding ASCII
        & $py -m pip install -r $filtered 2>&1 | Out-Null
        # torchaudio IS required by modern ComfyUI core (comfy/sd.py). Ensure it is
        # present AND matched to torch by installing from the SAME CUDA index (this
        # is what prevents the DLL mismatch -- a torchaudio from the default PyPI
        # index would not match a cu121 torch). Idempotent; downloads only if needed.
        & $py -m pip install torchaudio --index-url https://download.pytorch.org/whl/cu121 2>&1 | Out-Null
    }
    $launcher = Join-Path $root "start_comfyui.bat"
    $lines = @('@echo off', ('cd /d "' + $comfyDir + '"'), ('"' + $py + '" main.py --lowvram %*'))
    Set-Content -Path $launcher -Value $lines -Encoding ASCII
    Write-Host "      Wrote a ComfyUI launcher: $launcher" -ForegroundColor Green
    return $launcher
}

# Rewrite comfyui.root / comfyui.exe in config.yaml to match reality.
function Set-ComfyConfig($configPath, $root, $launcher) {
    if (-not (Test-Path $configPath)) { return }
    $rootY = ($root -replace '\\', '/')
    $exeY  = ($launcher -replace '\\', '/')
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($line in (Get-Content $configPath)) {
        if ($line -match '^(\s*)root:\s*".*"') { $out.Add($Matches[1] + 'root: "' + $rootY + '"          # set by verify') }
        elseif ($line -match '^(\s*)exe:\s*".*"') { $out.Add($Matches[1] + 'exe: "' + $exeY + '"                # set by verify') }
        else { $out.Add($line) }
    }
    Set-Content -Path $configPath -Value $out -Encoding UTF8
}

# Launch ComfyUI via the launcher, poll its API, then stop it. Returns $true if it
# came up. Proves the engine actually works (not just that files exist).
function Test-ComfyLaunch($launcher) {
    Write-Host "      Test-launching ComfyUI. First time is slow (imports + nodes)."
    # PowerShell requires DIFFERENT files for stdout vs stderr; we tail whichever
    # was written most recently for the live status line.
    $logOut = Join-Path $env:TEMP "dc_comfy_verify.out.log"
    $logErr = Join-Path $env:TEMP "dc_comfy_verify.err.log"
    foreach ($f in @($logOut, $logErr)) { if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue } }
    $proc = $null
    # Capture the engine's output so we can show WHAT it is doing while it loads.
    try { $proc = Start-Process -FilePath $launcher -PassThru -WindowStyle Minimized -RedirectStandardOutput $logOut -RedirectStandardError $logErr } catch {
        try { $proc = Start-Process -FilePath $launcher -PassThru -WindowStyle Minimized } catch { return $false }
    }
    $ok = $false
    # WALL-CLOCK cap of 150s: a healthy ComfyUI answers /system_stats within ~1-2 min
    # (server + node registration; the model loads later, on first gen). Past 150s it
    # is hung/failed, not slow -- so we stop instead of the old ~10-minute drag.
    $spin = @('|', '/', '-', '\'); $t0 = Get-Date; $i = 0; $capSecs = 150
    while (((Get-Date) - $t0).TotalSeconds -lt $capSecs) {
        try {
            $r = Invoke-WebRequest "http://127.0.0.1:8188/system_stats" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch {}
        # live status line: spinner + elapsed (of cap) + the last thing ComfyUI printed
        $secs = [int]((Get-Date) - $t0).TotalSeconds
        $last = ""
        try {
            $newest = @($logOut, $logErr) | Where-Object { Test-Path $_ } | Sort-Object { (Get-Item $_).LastWriteTime } -Descending | Select-Object -First 1
            if ($newest) { $last = (Get-Content $newest -Tail 1 -ErrorAction SilentlyContinue) }
        } catch {}
        if ($last.Length -gt 66) { $last = $last.Substring(0, 66) }
        Write-Host ("`r      $($spin[$i % 4]) waiting for ComfyUI  ${secs}s/${capSecs}s   $last".PadRight(110)) -NoNewline
        Start-Sleep -Seconds 2
        $i++
    }
    Write-Host ""   # end the status line
    if (-not $ok) {
        Write-Host "      ComfyUI did not respond. Last lines of its output:" -ForegroundColor Red
        foreach ($f in @($logErr, $logOut)) {
            if ((Test-Path $f) -and ((Get-Item $f).Length -gt 0)) {
                Get-Content $f -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "        $_" -ForegroundColor DarkGray }
            }
        }
    }
    # Stop the test instance; the app relaunches ComfyUI itself when it needs it.
    try {
        $cons = Get-NetTCPConnection -LocalPort 8188 -State Listen -ErrorAction SilentlyContinue
        if ($cons) { $cons.OwningProcess | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } }
        if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    } catch {}
    return $ok
}

# Orchestrate: heal the launcher, wire config, prove it starts.
function Verify-Comfy($root, $repoRoot, $configPath) {
    $mainPy = Find-ComfyMain $root
    if (-not $mainPy) {
        Write-Host "      ComfyUI is NOT installed at $root." -ForegroundColor Red
        Write-Host "      (Re-run the installer; if it keeps failing, 7-Zip or the download is the blocker.)" -ForegroundColor Red
        return $false
    }
    $launcher = Ensure-ComfyLauncher $root $repoRoot
    if (-not $launcher) {
        Write-Host "      ComfyUI found but no way to launch it (no python)." -ForegroundColor Red
        return $false
    }
    Set-ComfyConfig $configPath $root $launcher
    if (Test-ComfyLaunch $launcher) {
        Write-Host "      ComfyUI launches and responds. Engine OK." -ForegroundColor Green
        return $true
    }
    Write-Host "      ComfyUI is installed but did not respond when launched." -ForegroundColor Red
    # Save ComfyUI's own output to ONE obvious file so the reason is not lost in the
    # scrollback (the update keeps going after this).
    $errFile = Join-Path $repoRoot "comfyui_error.txt"
    try {
        $blocks = @("=== ComfyUI failed to start. Its last output: ===")
        foreach ($f in @((Join-Path $env:TEMP "dc_comfy_verify.err.log"),
                         (Join-Path $env:TEMP "dc_comfy_verify.out.log"))) {
            if ((Test-Path $f) -and ((Get-Item $f).Length -gt 0)) {
                $blocks += "`n--- $(Split-Path $f -Leaf) ---"
                $blocks += (Get-Content $f -Tail 60 -ErrorAction SilentlyContinue)
            }
        }
        Set-Content -Path $errFile -Value $blocks -Encoding UTF8
        Write-Host "      >>> The exact error was saved to:  $errFile" -ForegroundColor Yellow
        Write-Host "      >>> Open that file (or run Diagnostics.bat) and send it for a fix." -ForegroundColor Yellow
    } catch {}
    return $false
}
