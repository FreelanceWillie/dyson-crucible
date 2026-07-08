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
# Find a REAL Python 3.10+. Try the Windows launcher (py) FIRST -- bare 'python' on
# many Windows machines is the Microsoft Store stub that just prints "Python was not
# found". Store each candidate as exe + prefix args (e.g. 'py' '-3').
$PyExe = $null; $PyArgs = @()
foreach ($cand in @(@("py", @("-3")), @("python", @()), @("python3", @()))) {
    $exe = $cand[0]; $pre = $cand[1]
    try {
        $v = (& $exe @pre --version) 2>&1
        if ($LASTEXITCODE -eq 0 -and "$v" -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if (($maj -eq 3 -and $min -ge 10) -or ($maj -gt 3)) {
                $PyExe = $exe; $PyArgs = $pre
                Write-Host "      Found: $v  (using '$exe $($pre -join ' ')')"
                break
            }
        }
    } catch {}
}

if (-not $PyExe) {
    Write-Host ""
    Write-Host "  Python 3.10 or newer is required and was not found." -ForegroundColor Red
    Write-Host "  Install it from:  https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "  (Tick 'Add python.exe to PATH' in the installer.)" -ForegroundColor Red
    Write-Host "  If Python IS installed and 'py --version' works, just re-run this." -ForegroundColor Red
    Write-Host ""
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
    & $PyExe @PyArgs -m venv .venv
    if ($? -and (Test-Path $venvPy)) { Write-Host "      Created .venv" }
    else {
        Write-Host "      Could not create .venv. Try:  $PyExe $($PyArgs -join ' ') -m venv .venv" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "      .venv already exists, reusing it."
}

# Use the venv's python DIRECTLY for every install below. Do NOT rely on
# Activate.ps1 (the call operator does not persist activation into this scope,
# and bare 'python'/'pip' can resolve to the Microsoft Store stub).
& $venvPy -m pip install --upgrade pip

# ---------------------------------------------------------------------
# 3. torch + torchvision (CUDA 12.1 build)
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "[3/8] Installing torch + torchvision (CUDA 12.1)..." -ForegroundColor Yellow
Write-Host "      This download is large. Go make a coffee."
# CPU-only users: drop the --index-url line below. It will run, just slowly.
& $venvPy -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# ---------------------------------------------------------------------
# 4. Python dependencies
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "[4/8] Installing Python dependencies..." -ForegroundColor Yellow
& $venvPy -m pip install -r requirements.txt

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
Write-Host "   2. Double-click  'Dyson Crucible.bat'  to start the app."
Write-Host "      (It opens in your browser at http://127.0.0.1:7860 .)"
Write-Host "      CLI alternative:  .venv\Scripts\python.exe conductor\server.py"
Write-Host ""
