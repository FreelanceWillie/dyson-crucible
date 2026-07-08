# =====================================================================
#  Art Conductor - one-click bootstrap installer (Windows, PowerShell 5.1 safe)
# =====================================================================
#  Run from the repo root:   .\bootstrap.ps1
#  Optional: choose where ComfyUI installs:  .\bootstrap.ps1 -ComfyUIRoot "D:\AI\ComfyUI"
#
#  What it does, in order (see the [n/9] readout as it runs):
#    1. Python venv + deps      (delegates to setup.ps1)
#    2. Ollama app + model      (the local "brain")
#    3. ComfyUI portable/clone  (the generation engine)  -> a sibling ComfyUI folder
#    4. IPAdapter_plus node
#    5-8. Model files into ComfyUI/models/*
#    9. Wire config.yaml (comfyui.root + comfyui.exe) and print "ready".
#
#  It is IDEMPOTENT: anything already present is skipped, so it is safe to
#  re-run and it will resume where it stopped.
#
#  It is FORGIVING: every heavy step is wrapped so one failure prints a
#  clear manual fallback link and continues to the next step. You should
#  never see a raw stack trace.
#
#  PowerShell 5.1 notes honored: no '&&' chaining (';' / 'if ($?)' used),
#  native calls wrapped in try/catch, no '2>&1' on native exes.
# =====================================================================

param([string]$ComfyUIRoot = "")   # optional: where to install ComfyUI

$ErrorActionPreference = "Continue"

# ---------------------------------------------------------------------
#  EDIT-ME URLs and paths  (verify / bump these here in one place)
# ---------------------------------------------------------------------
# Portable: the repo is wherever this script lives. ComfyUI installs as a sibling
# folder next to the repo by default (override with the DC_COMFYUI_ROOT env var).
$RepoRoot     = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
# ComfyUI location: -ComfyUIRoot param > DC_COMFYUI_ROOT env var > a sibling folder.
if (-not $ComfyUIRoot) {
    $ComfyUIRoot = if ($env:DC_COMFYUI_ROOT) { $env:DC_COMFYUI_ROOT } else { Join-Path (Split-Path $RepoRoot -Parent) "ComfyUI" }
}

# ComfyUI standalone portable (official 7z on GitHub releases). If the
# version tag moves, update this ONE line. "latest" release page:
#   https://github.com/comfyanonymous/ComfyUI/releases
$ComfyUIPortableUrl = "https://github.com/comfyanonymous/ComfyUI/releases/latest/download/ComfyUI_windows_portable_nvidia.7z"
# Fallback if the portable download is unavailable: plain git clone.
$ComfyUIGitUrl      = "https://github.com/comfyanonymous/ComfyUI"
$IPAdapterGitUrl    = "https://github.com/cubiq/ComfyUI_IPAdapter_plus"
# LayerDiffuse: native transparent-background generation (gen.transparent: native).
# Optional; the tool works without it (gen.transparent: cut uses rembg instead).
$LayerDiffuseGitUrl = "https://github.com/huchenlei/ComfyUI-layerdiffuse"

# --- Model source URLs (HuggingFace direct download links) -----------
# SD1.5 base checkpoint. Comfy-Org mirror (the original runwayml repo was removed from HF).
$UrlCheckpoint = "https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors"
# DreamShaper 8: recommended default checkpoint (much better characters than base
# SD1.5, same VRAM). Falls back to the vanilla checkpoint above if this fails.
$UrlDreamShaper = "https://huggingface.co/Lykon/DreamShaper/resolve/main/DreamShaper_8_pruned.safetensors"
$CheckpointName = "DreamShaper_8_pruned.safetensors"  # rewritten to vanilla if DS download fails
# IP-Adapter models for SD1.5
$UrlIpAdapter     = "https://huggingface.co/h94/IP-Adapter/resolve/main/models/ip-adapter_sd15.bin"
$UrlIpAdapterPlus = "https://huggingface.co/h94/IP-Adapter/resolve/main/models/ip-adapter-plus_sd15.bin"
# CLIP-vision image encoder used by IP-Adapter (SD1.5 image encoder)
$UrlClipVision = "https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors"

# --- Ollama brain model ----------------------------------------------
$OllamaModel = "qwen2.5:3b-instruct"

# --- Derived model destination paths ---------------------------------
$CkptDir   = Join-Path $ComfyUIRoot "ComfyUI/models/checkpoints"
$IpDir     = Join-Path $ComfyUIRoot "ComfyUI/models/ipadapter"
$ClipVDir  = Join-Path $ComfyUIRoot "ComfyUI/models/clip_vision"
$NodesDir  = Join-Path $ComfyUIRoot "ComfyUI/custom_nodes"

$CkptFile        = Join-Path $CkptDir  "v1-5-pruned-emaonly.safetensors"
$DreamShaperFile = Join-Path $CkptDir  "DreamShaper_8_pruned.safetensors"
$IpFile          = Join-Path $IpDir    "ip-adapter_sd15.bin"
$IpPlusFile      = Join-Path $IpDir    "ip-adapter-plus_sd15.bin"
$ClipVisionFile  = Join-Path $ClipVDir "CLIP-ViT-H-14-image-encoder.safetensors"

# ---------------------------------------------------------------------
#  Small helpers
# ---------------------------------------------------------------------
function Step($n, $msg) {
    Write-Host ""
    Write-Host "[$n/9] $msg" -ForegroundColor Yellow
}
function Info($msg) { Write-Host "      $msg" }
function Ok($msg)   { Write-Host "      $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "      $msg" -ForegroundColor DarkYellow }
function Manual($msg) { Write-Host "      MANUAL FALLBACK: $msg" -ForegroundColor Cyan }

function Have-Cmd($name) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    return ($null -ne $c)
}

# Prerequisite self-assembly (Python 3.10+, Git) lives in one shared file so the
# update path (setup.ps1) uses the exact same logic. Ensure-Python / Ensure-Git.
. (Join-Path $RepoRoot "tools\prereqs.ps1")

# Download helper: idempotent, prints size when done, never throws.
function Get-File($url, $dest, $label) {
    if (Test-Path $dest) {
        Ok "$label already present, skipping."
        return $true
    }
    $dir = Split-Path $dest -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Info "Downloading $label ..."
    Info "  from $url"
    Info "  (large files can take a while; this window is not frozen.)"
    try {
        $ProgressPreference = "SilentlyContinue"   # faster + no spam in 5.1
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -ErrorAction Stop
        if (Test-Path $dest) {
            $mb = [math]::Round((Get-Item $dest).Length / 1MB, 1)
            Ok "$label downloaded ($mb MB)."
            return $true
        }
    } catch {
        Warn "Could not download $label automatically."
        Manual "Download this file yourself and save it to:"
        Info "        $dest"
        Info "        Source: $url"
        if (Test-Path $dest) { Remove-Item $dest -Force -ErrorAction SilentlyContinue }
        return $false
    }
    return $false
}

# ---------------------------------------------------------------------
#  Banner
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Art Conductor - one-click setup" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  This installs the local AI art stack for you:"
Write-Host "    Python + Ollama (brain) + ComfyUI (engine) + models."
Write-Host "  Grab a coffee. It is safe to re-run if it stops."
Write-Host "  Anything it cannot auto-install, it tells you the link to click."
Write-Host "=====================================================" -ForegroundColor Cyan

Set-Location $RepoRoot

# =====================================================================
#  [0/9] Prerequisites: self-assemble Python + Git on any machine
# =====================================================================
Step 0 "Checking prerequisites (Python, Git) and installing what is missing..."
$pyReady = Ensure-Python
Ensure-Git | Out-Null
if (-not $pyReady) {
    Write-Host ""
    Write-Host "  Cannot continue without Python. See the message above." -ForegroundColor Red
    Write-Host "  (Double-click this again after installing Python; it resumes.)" -ForegroundColor Red
    exit 1
}

# =====================================================================
#  [1/9] Python venv + dependencies  (delegate to setup.ps1)
# =====================================================================
Step 1 "Python environment + dependencies (venv, torch/CUDA, deps)..."
if (Test-Path (Join-Path $RepoRoot ".venv")) {
    Ok ".venv already exists. Re-running setup.ps1 to top up deps (safe)."
}
$setupPs1 = Join-Path $RepoRoot "setup.ps1"
if (Test-Path $setupPs1) {
    try {
        Info "Handing off to setup.ps1 (does Python, venv, torch CUDA 12.1, deps)..."
        & $setupPs1
        if ($?) { Ok "Python side complete." }
    } catch {
        Warn "setup.ps1 hit a problem."
        Manual "Open a terminal here and run:  .\setup.ps1"
    }
} else {
    Warn "setup.ps1 not found next to bootstrap.ps1."
    Manual "Install Python 3.10+ from https://www.python.org/downloads/ then re-run."
}

# =====================================================================
#  [2/9] Ollama (the local brain)
# =====================================================================
Step 2 "Installing Ollama (the local brain)..."
if (Have-Cmd "ollama") {
    Ok "Ollama already installed, skipping install."
} else {
    if (Have-Cmd "winget") {
        try {
            Info "Installing via winget (accepts licenses automatically)..."
            winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
            if ($?) { Ok "Ollama installed via winget." }
        } catch {
            Warn "winget install of Ollama failed."
            Manual "Download and run the installer from:  https://ollama.com/download"
        }
    } else {
        Warn "winget is not available on this machine."
        Manual "Download and run the Ollama installer from:  https://ollama.com/download"
        Info  "Then re-run bootstrap.ps1 to pull the model automatically."
    }
}

# Pull the model if ollama is now on PATH (fresh installs may need a new shell).
if (Have-Cmd "ollama") {
    Info "Pulling model '$OllamaModel' (one-time download, a few GB)..."
    try {
        ollama pull $OllamaModel
        if ($?) { Ok "Model '$OllamaModel' ready." }
    } catch {
        Warn "Could not pull the model automatically."
        Manual "In a new terminal run:  ollama pull $OllamaModel"
    }
} else {
    Warn "Ollama not on PATH yet."
    Manual "After installing Ollama, open a NEW terminal and run:  ollama pull $OllamaModel"
}

# =====================================================================
#  [3/9] ComfyUI (the generation engine)
# =====================================================================
Step 3 "Installing ComfyUI (the generation engine) -> $ComfyUIRoot ..."
$comfyPresent = (Test-Path (Join-Path $ComfyUIRoot "ComfyUI")) -or (Test-Path (Join-Path $ComfyUIRoot "run_nvidia_gpu.bat"))
if ($comfyPresent) {
    Ok "ComfyUI already present at $ComfyUIRoot, skipping."
} else {
    if (-not (Test-Path $ComfyUIRoot)) { New-Item -ItemType Directory -Force -Path $ComfyUIRoot | Out-Null }
    $archive = Join-Path $ComfyUIRoot "ComfyUI_windows_portable_nvidia.7z"
    $got = $false

    # Prefer the official portable build (self-contained python + launchers).
    Info "Trying the official portable build (recommended)..."
    $got = Get-File $ComfyUIPortableUrl $archive "ComfyUI portable archive"

    if ($got -and (Test-Path $archive)) {
        # Need 7-Zip to unpack the .7z.
        $sevenZip = $null
        foreach ($p in @("C:/Program Files/7-Zip/7z.exe", "C:/Program Files (x86)/7-Zip/7z.exe")) {
            if (Test-Path $p) { $sevenZip = $p; break }
        }
        if (-not $sevenZip -and (Have-Cmd "7z")) { $sevenZip = "7z" }

        if ($sevenZip) {
            try {
                Info "Extracting portable archive with 7-Zip..."
                & $sevenZip x $archive "-o$ComfyUIRoot" -y | Out-Null
                if ($?) { Ok "ComfyUI portable extracted."; Remove-Item $archive -Force -ErrorAction SilentlyContinue }
            } catch {
                Warn "Extraction failed."
                Manual "Right-click $archive and 'Extract Here' with 7-Zip, then re-run."
            }
        } else {
            Warn "7-Zip is not installed, cannot unpack the .7z automatically."
            Manual "Install 7-Zip from https://www.7-zip.org/ then extract:"
            Info   "        $archive  ->  $ComfyUIRoot"
            Info   "        Or delete that .7z and re-run with git installed for the clone path."
        }
    }

    # Fallback: git clone (needs models + deps handled by ComfyUI itself later).
    $nowPresent = (Test-Path (Join-Path $ComfyUIRoot "ComfyUI")) -or (Test-Path (Join-Path $ComfyUIRoot "run_nvidia_gpu.bat"))
    if (-not $nowPresent) {
        if (Have-Cmd "git") {
            try {
                Info "Falling back to 'git clone' of ComfyUI source..."
                git clone $ComfyUIGitUrl (Join-Path $ComfyUIRoot "ComfyUI")
                if ($?) { Ok "ComfyUI cloned. (Source build: it uses your repo .venv Python.)" }
            } catch {
                Warn "git clone of ComfyUI failed."
                Manual "Clone it yourself:  git clone $ComfyUIGitUrl `"$ComfyUIRoot/ComfyUI`""
            }
        } else {
            Warn "Neither portable extract nor git succeeded, and git is not installed."
            Manual "Easiest path: download the portable build and extract to $ComfyUIRoot :"
            Info   "        $ComfyUIPortableUrl"
            Info   "        Or install git and re-run to use the clone path."
        }
    }
}

# =====================================================================
#  [4/9] ComfyUI_IPAdapter_plus custom node
# =====================================================================
Step 4 "Installing the ComfyUI_IPAdapter_plus custom node (style steering)..."
$ipNodeDir = Join-Path $NodesDir "ComfyUI_IPAdapter_plus"
if (Test-Path $ipNodeDir) {
    Ok "IPAdapter_plus node already present, skipping."
} elseif (-not (Test-Path $NodesDir)) {
    Warn "ComfyUI/custom_nodes folder not found (ComfyUI may not have installed above)."
    Manual "Once ComfyUI is in place, run:  git clone $IPAdapterGitUrl `"$ipNodeDir`""
} else {
    if (Have-Cmd "git") {
        try {
            Info "Cloning IPAdapter_plus into custom_nodes..."
            git clone $IPAdapterGitUrl $ipNodeDir
            if ($?) { Ok "IPAdapter_plus node installed." }
        } catch {
            Warn "git clone of IPAdapter_plus failed."
            Manual "Clone it yourself:  git clone $IPAdapterGitUrl `"$ipNodeDir`""
        }
    } else {
        Warn "git is not installed."
        Manual "Install git, then run:  git clone $IPAdapterGitUrl `"$ipNodeDir`""
    }
}

# =====================================================================
#  [4b] ComfyUI-layerdiffuse custom node (OPTIONAL: native transparent gen)
# =====================================================================
Step 4 "Installing ComfyUI-layerdiffuse (optional: true transparent-background gen)..."
$ldNodeDir = Join-Path $NodesDir "ComfyUI-layerdiffuse"
if (Test-Path $ldNodeDir) {
    Ok "ComfyUI-layerdiffuse already present; re-applying compatibility patch."
} elseif (-not (Test-Path $NodesDir)) {
    Warn "custom_nodes folder not found; skipping LayerDiffuse (optional)."
    Manual "Later:  git clone $LayerDiffuseGitUrl `"$ldNodeDir`"  then  python tools/patch_layerdiffuse.py `"$ldNodeDir`""
} elseif (Have-Cmd "git") {
    try {
        if (-not (Test-Path $ldNodeDir)) {
            Info "Cloning ComfyUI-layerdiffuse into custom_nodes..."
            git clone $LayerDiffuseGitUrl $ldNodeDir
        }
        # Install the node's Python deps into ComfyUI's embedded python (portable).
        $embPy = Join-Path $ComfyUIRoot "python_embeded/python.exe"
        $ldReqs = Join-Path $ldNodeDir "requirements.txt"
        if ((Test-Path $embPy) -and (Test-Path $ldReqs)) {
            Info "Installing LayerDiffuse deps into ComfyUI's python..."
            & $embPy -m pip install -r $ldReqs 2>&1 | Out-Null
        }
        # Apply our compatibility patch (the upstream node breaks on current ComfyUI).
        # Idempotent: safe to re-run. Uses whatever python is on PATH (pure file edits).
        $patchPy = Join-Path $RepoRoot "tools/patch_layerdiffuse.py"
        if (Test-Path $patchPy) {
            # Prefer the repo .venv python, then ComfyUI's embedded python. Avoid
            # bare 'python' (Microsoft Store stub on many machines).
            $repoVenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
            $pyExe = if (Test-Path $repoVenvPy) { $repoVenvPy } elseif (Test-Path $embPy) { $embPy } else { $null }
            if ($pyExe) {
                & $pyExe $patchPy $ldNodeDir
                if ($?) { Ok "LayerDiffuse installed + patched (set gen.transparent: native to use it)." }
            } else {
                Warn "No python found to apply the LayerDiffuse patch."
                Manual "Run:  .venv\Scripts\python.exe tools/patch_layerdiffuse.py `"$ldNodeDir`""
            }
        }
    } catch {
        Warn "LayerDiffuse install/patch hit a problem (optional; 'cut' mode still works)."
        Manual "Manual:  git clone $LayerDiffuseGitUrl `"$ldNodeDir`"  then  python tools/patch_layerdiffuse.py `"$ldNodeDir`""
    }
} else {
    Warn "git not installed; skipping LayerDiffuse (optional)."
}

# =====================================================================
#  [5/9] SD1.5 checkpoint
# =====================================================================
Step 5 "Downloading the SD1.5 checkpoint (~2 GB) into models/checkpoints..."
# Prefer DreamShaper 8 (better characters). Fall back to base SD1.5 if it fails,
# and remember which one we got so Step 9 can point config.yaml at it.
$dsOk = Get-File $UrlDreamShaper $DreamShaperFile "DreamShaper 8 (recommended SD1.5 checkpoint)"
if (-not $dsOk) {
    Warn "DreamShaper download failed; falling back to base SD1.5."
    Get-File $UrlCheckpoint $CkptFile "SD1.5 checkpoint (v1-5-pruned-emaonly)" | Out-Null
    $CheckpointName = "v1-5-pruned-emaonly.safetensors"
}

# =====================================================================
#  [6/9] IP-Adapter model (base)
# =====================================================================
Step 6 "Downloading IP-Adapter model (base) into models/ipadapter..."
Get-File $UrlIpAdapter $IpFile "ip-adapter_sd15.bin" | Out-Null

# =====================================================================
#  [7/9] IP-Adapter model (plus)
# =====================================================================
Step 7 "Downloading IP-Adapter model (plus) into models/ipadapter..."
Get-File $UrlIpAdapterPlus $IpPlusFile "ip-adapter-plus_sd15.bin" | Out-Null

# =====================================================================
#  [8/9] CLIP-vision image encoder
# =====================================================================
Step 8 "Downloading the CLIP-vision image encoder into models/clip_vision..."
Get-File $UrlClipVision $ClipVisionFile "CLIP-vision image encoder" | Out-Null

# =====================================================================
#  [9/9] Wire config.yaml
# =====================================================================
Step 9 "Wiring config.yaml (comfyui.root + comfyui.exe)..."
$configPath = Join-Path $RepoRoot "config.yaml"

# The launcher we point comfyui.exe at. Portable builds ship run_nvidia_gpu.bat
# at the ComfyUI root. Prefer whichever launcher actually exists.
$launcher = ""
foreach ($cand in @(
    (Join-Path $ComfyUIRoot "run_nvidia_gpu.bat"),
    (Join-Path $ComfyUIRoot "ComfyUI/run_nvidia_gpu.bat"))) {
    if (Test-Path $cand) { $launcher = $cand; break }
}
if ($launcher -eq "") {
    # Not found (e.g. git-clone build has no .bat). Point at the expected
    # portable location anyway so the value is filled; note it below.
    $launcher = (Join-Path $ComfyUIRoot "run_nvidia_gpu.bat")
}
# Normalize to forward slashes to match the rest of config.yaml.
$rootForYaml     = ($ComfyUIRoot -replace '\\','/')
$launcherForYaml = ($launcher   -replace '\\','/')

if (Test-Path $configPath) {
    try {
        # Back up first.
        $backup = "$configPath.bak"
        Copy-Item $configPath $backup -Force
        Ok "Backed up config.yaml -> config.yaml.bak"

        $lines = Get-Content $configPath
        $out = New-Object System.Collections.Generic.List[string]
        foreach ($line in $lines) {
            if ($line -match '^(\s*)exe:\s*".*"') {
                $indent = $matches[1]
                $out.Add("$indent" + 'exe: "' + $launcherForYaml + '"                # set by bootstrap.ps1 (run with --lowvram on 4GB)')
            }
            elseif ($line -match '^(\s*)root:\s*".*"') {
                $indent = $matches[1]
                $out.Add("$indent" + 'root: "' + $rootForYaml + '"          # set by bootstrap.ps1')
            }
            elseif ($line -match '^(\s*)checkpoint:\s*\S') {
                $indent = $matches[1]
                $out.Add("$indent" + 'checkpoint: ' + $CheckpointName + '  # set by bootstrap.ps1 (downloaded checkpoint)')
            }
            else {
                $out.Add($line)
            }
        }
        Set-Content -Path $configPath -Value $out -Encoding UTF8
        Ok "config.yaml wired:"
        Info "  comfyui.root = $rootForYaml"
        Info "  comfyui.exe  = $launcherForYaml"
        if (-not (Test-Path $launcher)) {
            Warn "Note: that launcher .bat is not on disk yet (git-clone build has none)."
            Info "  If you used the source build, start ComfyUI with the repo .venv:"
            Info "    python `"$ComfyUIRoot/ComfyUI/main.py`" --lowvram"
        }
    } catch {
        Warn "Could not edit config.yaml automatically."
        Manual "Open config.yaml and under 'comfyui:' set:"
        Info   "        root: `"$rootForYaml`""
        Info   "        exe:  `"$launcherForYaml`""
    }
} else {
    Warn "config.yaml not found at $configPath."
    Manual "After the repo is in place, set comfyui.root and comfyui.exe there by hand."
}

# =====================================================================
#  You are ready
# =====================================================================
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "  You are ready" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  3 things to do to start making art:" -ForegroundColor Green
Write-Host ""
Write-Host "    1. Drop 8 to 20 style images into:  references\default\"
Write-Host "    2. Double-click  'Dyson Crucible.bat'  to start the app."
Write-Host "       (It auto-launches ComfyUI with --lowvram and opens your browser.)"
Write-Host "       CLI alternative:  .venv\Scripts\python.exe conductor\server.py"
Write-Host "    3. Open in your browser (if it does not open itself):  http://127.0.0.1:7860"
Write-Host ""
Write-Host "  The health panel (the Doctor) on that page shows anything still"
Write-Host "  missing (a model, Ollama, ComfyUI) and how to fix it. If a download"
Write-Host "  failed above, the Doctor and the messages here tell you the exact link."
Write-Host ""
Write-Host "  Tip: your card has 4GB of VRAM. Keep image size at 512 and let"
Write-Host "  ComfyUI run with --lowvram (already wired). SDXL is not recommended;"
Write-Host "  stick with SD1.5."
Write-Host ""

# Mark the install as complete so the launcher knows it does not need to run
# bootstrap again. A checkpoint present is our proxy for "the heavy install ran".
$ckptOk = (Test-Path (Join-Path $CkptDir "DreamShaper_8_pruned.safetensors")) -or (Test-Path $CkptFile)
if ($ckptOk) {
    Set-Content -Path (Join-Path $RepoRoot ".dc_installed") -Value (Get-Date -Format "s") -Encoding ASCII
}
