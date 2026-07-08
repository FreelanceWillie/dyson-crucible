# update.ps1 - update an existing Dyson Crucible install in place.
# Safe to run any time. Preserves your config.yaml, pulls the latest code,
# tops up Python deps, and re-applies the custom-node compatibility patches.
#
#   .\update.ps1
#
# After it finishes, restart the app (python conductor/server.py).

$ErrorActionPreference = "Continue"
$RepoRoot   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ComfyRoots = @()
if ($env:DC_COMFYUI_ROOT) { $ComfyRoots += $env:DC_COMFYUI_ROOT }
$ComfyRoots += (Join-Path (Split-Path $RepoRoot -Parent) "ComfyUI")

function Say($m)  { Write-Host "[update] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[ok]     $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn]   $m" -ForegroundColor Yellow }

Set-Location $RepoRoot

# 1. Back up config + pull latest (autostash re-applies your local config edits).
if (Test-Path "config.yaml") { Copy-Item "config.yaml" "config.yaml.bak" -Force; Ok "backed up config.yaml -> config.yaml.bak" }

Say "pulling latest code..."
$before = (git rev-parse --short HEAD 2>$null)
git pull --autostash --no-edit
$after = (git rev-parse --short HEAD 2>$null)
if ($before -eq $after) { Ok "already up to date ($after)." } else { Ok "updated $before -> $after." }

# If the pull clobbered config.yaml (rare merge case), restore the user's copy.
if ((Test-Path "config.yaml.bak") -and (Test-Path "config.yaml")) {
    $cur = Get-Content "config.yaml" -Raw -ErrorAction SilentlyContinue
    if (-not $cur -or $cur.Trim().Length -lt 10) {
        Copy-Item "config.yaml.bak" "config.yaml" -Force; Warn "restored config.yaml from backup."
    }
}

# 2. Top up Python deps (setup.ps1 is idempotent).
if (Test-Path (Join-Path $RepoRoot "setup.ps1")) {
    Say "topping up Python dependencies..."
    try { & (Join-Path $RepoRoot "setup.ps1") | Out-Null; Ok "deps current." }
    catch { Warn "setup.ps1 had a problem; run it manually if something is missing." }
}

# 3. Re-apply custom-node patches for any installed feature-pack nodes (idempotent).
$patch = Join-Path $RepoRoot "tools/patch_layerdiffuse.py"
foreach ($cr in $ComfyRoots) {
    foreach ($nd in @((Join-Path $cr "custom_nodes/ComfyUI-layerdiffuse"),
                      (Join-Path $cr "ComfyUI/custom_nodes/ComfyUI-layerdiffuse"))) {
        if ((Test-Path $nd) -and (Test-Path $patch)) {
            Say "re-patching ComfyUI-layerdiffuse..."
            $py = if (Test-Path (Join-Path $cr "python_embeded/python.exe")) { Join-Path $cr "python_embeded/python.exe" } else { "python" }
            & $py $patch $nd
        }
    }
}

# 4. Pull latest for installed feature-pack nodes (so they track upstream fixes too).
foreach ($cr in $ComfyRoots) {
    $nodes = Join-Path $cr "custom_nodes"
    if (Test-Path $nodes) {
        foreach ($d in Get-ChildItem $nodes -Directory -ErrorAction SilentlyContinue) {
            if (Test-Path (Join-Path $d.FullName ".git")) {
                & git -C $d.FullName pull --ff-only 2>$null | Out-Null
            }
        }
        # a node we patch must be re-patched after its own pull. Use the repo .venv
        # python (avoid bare 'python' = Microsoft Store stub on many machines).
        $ld = Join-Path $nodes "ComfyUI-layerdiffuse"
        $repoVenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
        $py2 = if (Test-Path $repoVenvPy) { $repoVenvPy } else { $null }
        if ($py2 -and (Test-Path $ld) -and (Test-Path $patch)) { & $py2 $patch $ld 2>$null | Out-Null }
    }
}

Ok "Update complete. Start the app by double-clicking 'Dyson Crucible.bat'."
