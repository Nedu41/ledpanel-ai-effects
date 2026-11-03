import json
import os
import re
import sys
from pathlib import Path

from PIL import Image
import numpy as np

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "panel-src" / "araclar"))
sys.argv = ["ai_efekt.py"]
import ai_efekt as engine

event = json.loads(Path(os.environ["EVENT_PATH"]).read_text(encoding="utf-8"))
payload = event.get("client_payload", {})
effect_id = payload.get("id", "")
if not re.fullmatch(r"[0-9]+-[0-9a-f-]+\.gif", effect_id):
    raise SystemExit("invalid effect id")
code = payload.get("code", "")
if not isinstance(code, str) or len(code) > 48000:
    raise SystemExit("invalid generated code")
w, h = int(payload.get("w", 0)), int(payload.get("h", 0))
if (w, h) not in ((104, 52), (52, 104)):
    raise SystemExit("unsupported panel dimensions")
fn, error = engine.compile_effect(code, w, h)
if not fn:
    raise SystemExit("Generated effect rejected: " + str(error)[:500])

frames = []
count, fps = 90, 15
for i in range(count):
    arr = np.asarray(fn(i / fps, w, h))
    if arr.shape != (h, w, 3):
        raise SystemExit("frame() returned invalid shape")
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    frames.append(Image.fromarray(arr, "RGB").quantize(colors=64, method=Image.Quantize.MEDIANCUT))

out = Path("out")
out.mkdir(exist_ok=True)
target = out / effect_id
frames[0].save(target, save_all=True, append_images=frames[1:], duration=67, loop=0, optimize=False, disposal=2)
if target.stat().st_size > 12_000_000:
    raise SystemExit("Rendered animation exceeds the library size limit")
print(f"Rendered {count} frames at {w}x{h}: {target.stat().st_size} bytes")