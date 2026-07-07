# Helios

A little art studio that runs entirely on your own PC, for free, and draws in your style so you never have to.

**What you can do:** describe a hero in plain English, watch it draw a few options that match the look of your reference art, pick the ones you like, and refine them just by chatting ("too wizard-y, make him an evil warlock"). When you are happy, turn a winner into a clean vector (SVG) with one click. No subscriptions, no accounts, no cloud.

---

## Quick start

You install three things once, then you are set.

1. **Install Python** (version 3.10 or newer) from https://www.python.org/downloads/
   During install, tick **"Add python.exe to PATH"**.
2. **Install ComfyUI** (the drawing engine) from https://github.com/comfyanonymous/ComfyUI
   Drop an SD1.5 checkpoint into `ComfyUI/models/checkpoints/` and add the `ComfyUI_IPAdapter_plus` custom node.
3. **Install Ollama** (the assistant that rewrites your descriptions) from https://ollama.com/download
4. **Run the setup script.** In a terminal, in this folder:

   ```
   .\setup.ps1
   ```

   (On Mac or Linux: `bash setup.sh`)

5. **Add your reference art.** Put 8 to 20 images you like into the `references/default/` folder.
6. **Start the engines.** Launch ComfyUI, and in another terminal run `ollama serve`.
7. **Run Helios and open it in your browser:**

   ```
   python conductor/server.py
   ```

   Then open http://127.0.0.1:7860

That is it. You are ready to make art.

---

## How you actually use it

It feels like texting an artist who never gets tired.

1. Type what you want, for example: **a menacing frost knight**.
2. It draws **4 options** in your style.
3. You do not like them? Just say what to change: **more armor, less blue**.
4. It redraws with your notes baked in.
5. When one looks right, click it to keep it.
6. Click **Vectorize** to turn your favorite into a clean SVG you can drop into your game.

Each candidate is quietly scored against your reference images, so the ones closest to your taste float to the top. Your eye still makes the final call.

---

## The reference folder

The `references/default/` folder is where you teach it your style. Drop in 8 to 20 pictures that share one clear look. A consistent set of ten beats a messy set of thirty.

---

## If something breaks

- **Nothing generates / "cannot reach ComfyUI".** ComfyUI is not running. Start it, wait until it finishes loading, then try again.
- **Refining does nothing / "cannot reach the brain".** Ollama is not running. Open a terminal and run `ollama serve`.
- **Generation feels slow.** On a 4GB graphics card this is normal. A batch takes a little while. Grab a coffee; it is still free.
- **"Out of memory" / VRAM errors.** Lower the image size in the dashboard settings, and make sure you are using SD1.5 (not SDXL, which is too heavy for 4GB).
- **First run is slow to start.** The very first time, it downloads the model files. Later runs are quick.

Everything stays on your machine. Nothing is uploaded, nothing costs money.
