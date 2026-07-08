# Setup: get Art Conductor running

This is the short, no-jargon version. One command does almost everything.

## 1. Run the installer

Open PowerShell in the Art Conductor folder (`E:\Tools\art-conductor`) and run:

```powershell
.\bootstrap.ps1
```

Then wait. It installs the whole stack for you and prints a running checklist
like `[3/9] Installing ComfyUI...` so you always know what it is doing and how
much is left. The big model downloads take a while; the window is not frozen.

It is safe to leave and come back. If it stops for any reason, just run
`.\bootstrap.ps1` again and it picks up where it left off (anything already
installed is skipped).

If a step cannot install itself, it does not crash. It prints one link to click
and keeps going. The most common one is Ollama: if you see a note about it, open
[https://ollama.com/download](https://ollama.com/download), run the installer,
then run `.\bootstrap.ps1` again to finish.

## 2. Add your style images

Drop 8 to 20 reference images (the look you want) into:

```
references\default\
```

## 3. Start everything and open the browser

In the Art Conductor folder:

```powershell
.\.venv\Scripts\Activate.ps1
python conductor\server.py
```

Then open [http://127.0.0.1:7860](http://127.0.0.1:7860) in your browser.

The conductor can start the generation engine (ComfyUI) for you, using the
launcher the installer wired up, with the low-memory setting your graphics card
needs.

## 4. Check the health panel

On that page there is a health panel (the Doctor). It shows anything still
missing (a model file, Ollama, ComfyUI) and tells you how to fix it. If a
download failed during setup, this is where you will see it.

---

## Troubleshooting (quick list)

- **"Ollama not found."** Install it from
  [https://ollama.com/download](https://ollama.com/download), then re-run
  `.\bootstrap.ps1`. It will pull the brain model for you.

- **ComfyUI is very slow the first time.** That is normal. The first generation
  loads the model into memory. Later runs are much faster.

- **"Out of memory" / VRAM error.** Your card has 4GB. Keep the image size at
  512 (the default) and make sure ComfyUI is running in low-memory mode
  (`--lowvram`, already wired for you). Do not use SDXL on this card; stick with
  SD1.5.

- **A model download failed.** The installer prints the exact file and the link
  to get it manually, plus the folder to drop it into. Download it there, then
  re-run `.\bootstrap.ps1` (it skips everything already done).

- **Ollama was just installed but the model would not pull.** Ollama needs a
  fresh terminal after install. Close PowerShell, open a new one, and run
  `.\bootstrap.ps1` again (or just `ollama pull qwen2.5:7b-instruct`).

## Transparent-background art

Stable Diffusion 1.5 has no native alpha channel, so it cannot draw on true
transparency by itself. Two ways to get transparent PNGs, from easy to advanced:

1. **Recommended (built in, zero setup).** Generate normally, then cut the
   background off. Either flip `gen.transparent: true` in `config.yaml` (every
   candidate comes out pre-cut), or after picking a winner run the **`game_sprite`**
   or **`pixel_sprite`** look-lab preset. Both use `rembg` to remove the
   background and give you a clean RGBA PNG (the pixel one cuts *first*, then
   pixelates, so there is no white halo on the edges). This works great and needs
   nothing extra.

2. **Advanced (native, optional).** For true transparent *generation* you can add
   the [ComfyUI-LayerDiffuse](https://github.com/huchenlei/ComfyUI-layerdiffuse)
   custom node plus its transparent model. It renders real alpha directly, but it
   is an extra ~1.5GB download and uses more VRAM, which is tight on a 4GB card.
   Only bother if the auto-cut in option 1 is not clean enough for a specific
   asset. Most users never need this.
