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

# 1. Back up config + sync to the latest published code.
if (Test-Path "config.yaml") { Copy-Item "config.yaml" "config.yaml.bak" -Force; Ok "backed up config.yaml -> config.yaml.bak" }

Say "syncing latest code..."
$before = (git rev-parse --short HEAD 2>$null)
# Hard-sync to origin instead of a merge-pull: this is a one-way "get the latest
# published version" for a user who never commits, and it stays robust even if
# upstream history was rewritten (a normal 'git pull' would then fail to merge).
# Only TRACKED files are touched; config.yaml, projects/, models, and outputs are
# gitignored (user data) and are left untouched.
git fetch origin --prune 2>$null
$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
if (-not $branch -or $branch -eq "HEAD") { $branch = "master" }
git reset --hard ("origin/" + $branch) 2>$null
$after = (git rev-parse --short HEAD 2>$null)
if ($before -eq $after) { Ok "already up to date ($after)." } else { Ok "updated $before -> $after." }

# Safety net: if config.yaml went missing/empty for any reason, restore the backup.
if ((Test-Path "config.yaml.bak") -and (-not (Test-Path "config.yaml"))) {
    Copy-Item "config.yaml.bak" "config.yaml" -Force; Warn "restored config.yaml from backup."
} elseif ((Test-Path "config.yaml.bak") -and (Test-Path "config.yaml")) {
    $cur = Get-Content "config.yaml" -Raw -ErrorAction SilentlyContinue
    if (-not $cur -or $cur.Trim().Length -lt 10) {
        Copy-Item "config.yaml.bak" "config.yaml" -Force; Warn "restored config.yaml from backup."
    }
}

# 2. Full idempotent assembly: run bootstrap (Python deps + ComfyUI + models +
#    config). It skips anything already present, so this both APPLIES the update
#    and FILLS a partial install (e.g. code was updated but ComfyUI/models were
#    never installed -- exactly what happens if you only ever ran the deps step).
if (Test-Path (Join-Path $RepoRoot "bootstrap.ps1")) {
    Say "running the installer to top up code, engine, and models (idempotent)..."
    try { & (Join-Path $RepoRoot "bootstrap.ps1") } catch { Warn "bootstrap had a problem; see the messages above." }
}

# 3. Re-apply custom-node patches for any installed feature-pack nodes (idempotent).
$patch = Join-Path $RepoRoot "tools/patch_layerdiffuse.py"
foreach ($cr in $ComfyRoots) {
    foreach ($nd in @((Join-Path $cr "custom_nodes/ComfyUI-layerdiffuse"),
                      (Join-Path $cr "ComfyUI/custom_nodes/ComfyUI-layerdiffuse"))) {
        if ((Test-Path $nd) -and (Test-Path $patch)) {
            Say "re-patching ComfyUI-layerdiffuse..."
            $repoVenvPy0 = Join-Path $RepoRoot ".venv\Scripts\python.exe"
            $py = if (Test-Path (Join-Path $cr "python_embeded/python.exe")) { Join-Path $cr "python_embeded/python.exe" }
                  elseif (Test-Path $repoVenvPy0) { $repoVenvPy0 } else { $null }
            if ($py) { & $py $patch $nd }
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
