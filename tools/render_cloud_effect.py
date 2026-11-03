import json
import math
import os
import re
import sys
import time
import traceback
from pathlib import Path

from PIL import Image
import numpy as np

root = Path(__file__).resolve().parents[1]


def compile_effect(code, W, H):
    """Validate and compile the existing LED frame(t, W, H) Python format."""
    code = re.sub(r'^\s*(import (numpy|math)( as \w+)?|from (numpy|math) import .*)\s*$', '', code, flags=re.M)
    bad = re.search(r'^\s*(import|from)\s.*$', code, re.M) or re.search(r'__\w*', code) or re.search(r'\b(open|exec|eval|compile|globals|locals|getattr|setattr|input)\s*\(', code)
    if bad:
        return None, "yasak ifade: %r" % bad.group(0).strip()[:60]
    allowed = ('range', 'len', 'min', 'max', 'abs', 'int', 'float', 'sum', 'round', 'enumerate', 'zip', 'list', 'dict', 'tuple', 'bool', 'pow', 'divmod', 'sorted', 'reversed', 'map', 'any', 'all', 'isinstance', 'set')
    builtins_obj = __builtins__
    safe_builtins = {k: builtins_obj[k] if isinstance(builtins_obj, dict) else getattr(builtins_obj, k) for k in allowed}
    ns = {'np': np, 'math': math, '__builtins__': safe_builtins}
    try:
        exec(code, ns)
        fn = ns['frame']
        started = time.time()
        for tt in (0.0, 1.7):
            arr = np.asarray(fn(tt, W, H))
            if arr.shape != (H, W, 3):
                return None, 'frame() sekli %s, beklenen %s' % (arr.shape, (H, W, 3))
        elapsed = (time.time() - started) / 2
        if elapsed > 0.12:
            return None, 'frame() cok yavas (%.0f ms)' % (elapsed * 1000)
        return fn, None
    except Exception:
        return None, traceback.format_exc(limit=3)

event = json.loads(Path(os.environ["EVENT_PATH"]).read_text(encoding="utf-8"))
payload = event.get("client_payload", {})
effect_id = str(payload.get("id", ""))
out = Path("out")
out.mkdir(exist_ok=True)
if not re.fullmatch(r"[0-9]+-[0-9a-f-]+\.gif", effect_id):
    raise SystemExit("invalid effect id")
error_path = out / (effect_id[:-4] + ".error.json")
try:
    code = payload.get("code", "")
    if not isinstance(code, str) or len(code) > 48000:
        raise ValueError("invalid generated code")
    w, h = int(payload.get("w", 0)), int(payload.get("h", 0))
    if (w, h) not in ((104, 52), (52, 104)):
        raise ValueError("unsupported panel dimensions")
    fn, error = compile_effect(code, w, h)
    if not fn:
        raise ValueError("AI code check: " + str(error)[:300])
    frames = []
    # Match the original Pico effect path: 48 frames at 15 fps gives a
    # smooth ~3.2 second loop while staying within Pico flash and upload time.
    count, fps = 48, 15
    for i in range(count):
        arr = np.asarray(fn(i / fps, w, h))
        if arr.shape != (h, w, 3):
            raise ValueError("frame() returned invalid shape")
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        frames.append(Image.fromarray(arr, "RGB").quantize(colors=64, method=Image.Quantize.MEDIANCUT))
    target = out / effect_id
    frames[0].save(target, save_all=True, append_images=frames[1:], duration=67, loop=0, optimize=False, disposal=2)
    if target.stat().st_size > 12_000_000:
        target.unlink(missing_ok=True)
        raise ValueError("rendered animation exceeds the library size limit")
    print(f"Rendered {count} frames at {w}x{h}: {target.stat().st_size} bytes")
except Exception as exc:
    (out / (effect_id[:-4] + ".gif")).unlink(missing_ok=True)
    error_path.write_text(json.dumps({"error": str(exc)[:300]}), encoding="utf-8")
    print("Animation generation failed:", str(exc)[:300])
