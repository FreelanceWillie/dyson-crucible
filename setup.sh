#!/usr/bin/env bash
# Helios - Linux / macOS setup (bash)
#
# Run from the repo root:  bash setup.sh
#
# Forgiving by design: optional checks warn and continue; only a missing
# Python stops the show.

set +e

echo ""
echo "====================================================="
echo " Helios - setup"
echo "====================================================="
echo ""

# ---------------------------------------------------------------------
# 1. Python 3.10+
# ---------------------------------------------------------------------
echo "[1/8] Checking Python..."
PYBIN=""
if command -v python3 >/dev/null 2>&1; then PYBIN="python3"; fi
if [ -z "$PYBIN" ] && command -v python >/dev/null 2>&1; then PYBIN="python"; fi

PY_OK=0
if [ -n "$PYBIN" ]; then
    PYVER="$($PYBIN --version 2>&1)"
    echo "      Found: $PYVER"
    if $PYBIN -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
        PY_OK=1
    fi
fi

if [ "$PY_OK" -ne 1 ]; then
    echo ""
    echo "  Python 3.10 or newer is required and was not found."
    echo "  Install it from:  https://www.python.org/downloads/"
    echo "  (or your package manager, e.g. apt install python3 python3-venv)"
    echo ""
    echo "  Stopping here. Re-run setup.sh once Python is installed."
    exit 1
fi

# ---------------------------------------------------------------------
# 2. Virtual environment
# ---------------------------------------------------------------------
echo ""
echo "[2/8] Creating virtual environment (.venv)..."
if [ ! -d ".venv" ]; then
    $PYBIN -m venv .venv && echo "      Created .venv"
else
    echo "      .venv already exists, reusing it."
fi

echo "      Activating .venv..."
# shellcheck disable=SC1091
. ./.venv/bin/activate && echo "      Activated."

python -m pip install --upgrade pip

# ---------------------------------------------------------------------
# 3. torch + torchvision (CUDA 12.1 build)
# ---------------------------------------------------------------------
echo ""
echo "[3/8] Installing torch + torchvision (CUDA 12.1)..."
echo "      This download is large. Go make a coffee."
# CPU-only users: drop the --index-url line below. It will run, just slowly.
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# ---------------------------------------------------------------------
# 4. Python dependencies
# ---------------------------------------------------------------------
echo ""
echo "[4/8] Installing Python dependencies..."
pip install -r requirements.txt

# ---------------------------------------------------------------------
# 5. GPU check
# ---------------------------------------------------------------------
echo ""
echo "[5/8] Checking your GPU..."
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
    echo "      4GB of VRAM is fine for Stable Diffusion 1.5."
    echo "      SDXL is NOT recommended on 4GB. Stick with SD1.5."
else
    echo "      nvidia-smi not found. That's OK if you have no NVIDIA GPU."
    echo "      Generation will run on CPU, which is much slower."
fi

# ---------------------------------------------------------------------
# 6. Ollama (the local brain)
# ---------------------------------------------------------------------
echo ""
echo "[6/8] Checking Ollama (the local conductor brain)..."
if command -v ollama >/dev/null 2>&1; then
    echo "      Found: $(ollama --version)"
    echo "      Pulling model qwen2.5:7b-instruct (one-time download)..."
    ollama pull qwen2.5:7b-instruct
else
    echo "      Ollama not found (this is not fatal)."
    echo "      Install it from:  https://ollama.com/download"
    echo "      Then run:  ollama pull qwen2.5:7b-instruct"
    echo "      (You can also use a free Google AI Studio key or the claude CLI"
    echo "       as the brain instead. See config.yaml.)"
fi

# ---------------------------------------------------------------------
# 7. ComfyUI (the generation engine) - separate install
# ---------------------------------------------------------------------
echo ""
echo "[7/8] ComfyUI (the generation engine)"
echo "      ComfyUI is installed separately. Grab it here:"
echo "        https://github.com/comfyanonymous/ComfyUI"
echo ""
echo "      After installing ComfyUI you need:"
echo "        - An SD1.5 checkpoint in  ComfyUI/models/checkpoints/"
echo "        - The custom node  ComfyUI_IPAdapter_plus  (for style steering)"
echo ""
echo "      Optional: set  comfyui.exe  in config.yaml to your ComfyUI"
echo "      launcher so Helios can start it for you automatically."

# ---------------------------------------------------------------------
# 8. Done
# ---------------------------------------------------------------------
echo ""
echo "====================================================="
echo " Setup complete"
echo "====================================================="
echo ""
echo " To start making art:"
echo ""
echo "   1. Put 8 to 20 style images in  references/default/"
echo "   2. Start ComfyUI, and in another terminal run:  ollama serve"
echo "   3. Run:  python conductor/server.py"
echo "      Then open:  http://127.0.0.1:7860"
echo ""
