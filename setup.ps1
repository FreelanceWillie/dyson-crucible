# Dyson Crucible - Windows setup (PowerShell 5.1 safe)
#
# Run from the repo root:  .\setup.ps1
#
# This script is forgiving. Optional checks warn and continue; only a
# missing Python stops the show. No && operators, no piping native exe
# stderr, native-tool probes wrapped in try/catch.

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host " Dyson Crucible - setup" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------
# 1. Python 3.10+
# ---------------------------------------------------------------------
Write-Host "[1/8] Checking Python..." -ForegroundColor Yellow
$pyOk = $false
try {
    $pyVer = (python --version)
    if ($?) {
        Write-Host "      Found: $pyVer"
        $m = [regex]::Match($pyVer, "(\d+)\.(\d+)")
        if ($m.Success) {
            $maj = [int]$m.Groups[1].Value
            $min = [int]$m.Groups[2].Value
            if ($maj -eq 3 -and $min -ge 10) { $pyOk = $true }
            elseif ($maj -gt 3) { $pyOk = $true }
        }
    }
} catch {
    $pyOk = $false
}

if (-not $pyOk) {
    Write-Host ""
    Write-Host "  Python 3.10 or newer is required and was not found." -ForegroundColor Red
    Write-Host "  Install it from:  https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "  (Tick 'Add python.exe to PATH' in the installer.)" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Stopping here. Re-run setup.ps1 once Python is installed." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------
# 2. Virtual environment
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "[2/8] Creating virtual environment (.venv)..." -ForegroundColor Yellow
$venvPy = ".\.venv\Scripts\python.exe"
# Self-heal a partial/broken venv (folder exists but the interpreter is missing --
# what happens if a first install was interrupted). Rebuild it cleanly.
if ((Test-Path ".venv") -and -not (Test-Path $venvPy)) {
    Write-Host "      .venv looks incomplete (interrupted install); rebuilding it..." -ForegroundColor Yellow
    Remove-Item ".venv" -Recurse -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path $venvPy)) {
    python -m venv .venv
    if ($? -and (Test-Path $venvPy)) { Write-Host "      Created .venv" }
    else { Write-Host "      Could not create .venv (is Python installed and on PATH?)" -ForegroundColor Red }
} else {
    Write-Host "      .venv already exists, reusing it."
}

Write-Host "      Activating .venv..."
& ".\.venv\Scripts\Activate.ps1"
if ($?) { Write-Host "      Activated." }

python -m pip install --upgrade pip

# ---------------------------------------------------------------------
# 3. torch + torchvision (CUDA 12.1 build)
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "[3/8] Installing torch + torchvision (CUDA 12.1)..." -ForegroundColor Yellow
Write-Host "      This download is large. Go make a coffee."
# CPU-only users: drop the --index-url line below. It will run, just slowly.
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# ---------------------------------------------------------------------
# 4. Python dependencies
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "[4/8] Installing Python dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt

# ---------------------------------------------------------------------
# 5. GPU check
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "[5/8] Checking your GPU..." -ForegroundColor Yellow
try {
    $smi = (nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)
    if ($?) {
        Write-Host "      GPU: $smi"
        Write-Host "      4GB of VRAM is fine for Stable Diffusion 1.5."
        Write-Host "      SDXL is NOT recommended on 4GB. Stick with SD1.5."
    }
} catch {
    Write-Host "      nvidia-smi not found. That's OK if you have no NVIDIA GPU." -ForegroundColor DarkYellow
    Write-Host "      Generation will run on CPU, which is much slower." -ForegroundColor DarkYellow
}

# ---------------------------------------------------------------------
# 6. Ollama (the local brain)
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "[6/8] Checking Ollama (the local conductor brain)..." -ForegroundColor Yellow
$ollamaOk = $false
try {
    $olVer = (ollama --version)
    if ($?) { $ollamaOk = $true; Write-Host "      Found: $olVer" }
} catch {
    $ollamaOk = $false
}

if ($ollamaOk) {
    Write-Host "      Pulling model qwen2.5:3b-instruct (one-time download)..."
    ollama pull qwen2.5:3b-instruct
} else {
    Write-Host "      Ollama not found (this is not fatal)." -ForegroundColor DarkYellow
    Write-Host "      Install it from:  https://ollama.com/download" -ForegroundColor DarkYellow
    Write-Host "      Then run:  ollama pull qwen2.5:3b-instruct" -ForegroundColor DarkYellow
    Write-Host "      (You can also use a free Google AI Studio key or the claude CLI"
    Write-Host "       as the brain instead. See config.yaml.)"
}

# ---------------------------------------------------------------------
# 7. ComfyUI (the generation engine) - separate install
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "[7/8] ComfyUI (the generation engine)" -ForegroundColor Yellow
Write-Host "      ComfyUI is installed separately. Grab it here:"
Write-Host "        https://github.com/comfyanonymous/ComfyUI"
Write-Host ""
Write-Host "      After installing ComfyUI you need:"
Write-Host "        - An SD1.5 checkpoint in  ComfyUI/models/checkpoints/"
Write-Host "        - The custom node  ComfyUI_IPAdapter_plus  (for style steering)"
Write-Host ""
Write-Host "      Optional: set  comfyui.exe  in config.yaml to your ComfyUI"
Write-Host "      launcher so Dyson Crucible can start it for you automatically."

# ---------------------------------------------------------------------
# 8. Done
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host " Setup complete" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Write-Host " To start making art:" -ForegroundColor Green
Write-Host ""
Write-Host "   1. Put 8 to 20 style images in  references\default\"
Write-Host "   2. Start ComfyUI, and in another terminal run:  ollama serve"
Write-Host "   3. Run:  python conductor\server.py"
Write-Host "      Then open:  http://127.0.0.1:7860"
Write-Host ""
