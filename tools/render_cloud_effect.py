import json
import math
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
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
        return None, short_error()


def short_error():
    """Last traceback frames from the generated code plus the exception text (the useful part for a repair request)."""
    lines = [l for l in traceback.format_exc().splitlines() if l.strip()]
    tail = [l for l in lines if '<string>' in l][-2:] + lines[-1:]
    return '\n'.join(tail)[-700:]

event = json.loads(Path(os.environ["EVENT_PATH"]).read_text(encoding="utf-8"))
payload = event.get("client_payload", {})
effect_id = str(payload.get("id", ""))
out = Path("out")
out.mkdir(exist_ok=True)
if not re.fullmatch(r"[0-9]+-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?:-[a-z0-9]+)*\.gif", effect_id):
    raise SystemExit("invalid effect id")
error_path = out / (effect_id[:-4] + ".error.json")
FRAME_MS = 70


def render_frames(code, w, h):
    """Compile and render one seamless loop; raises ValueError with a short, repairable error."""
    fn, error = compile_effect(code, w, h)
    if not fn:
        raise ValueError(error)
    # 72 frames of 70 ms (~5 s; GIF stores 1/100 s, so 70 ms is exact while 67 ms would play as 60).
    # XF extra frames are rendered and cross-faded into the first frames: no visible jump at the loop point.
    count, fps, XF = 72, 1000 / 70, 12
    raw = []
    for i in range(count + XF):
        try:
            arr = np.asarray(fn(i / fps, w, h))
        except Exception:
            raise ValueError(short_error())
        if arr.shape != (h, w, 3):
            raise ValueError("frame() returned shape %s at t=%.2f, expected %s" % (arr.shape, i / fps, (h, w, 3)))
        raw.append(np.clip(np.asarray(arr, dtype=np.float32), 0, 255))
    frames = []
    for i in range(count):
        arr = raw[i]
        if i < XF:
            a = (i + 1) / (XF + 1)
            arr = arr * a + raw[i + count] * (1 - a)
        # 256 colours, no dithering: dither noise flickers between frames on the LED panel.
        img = Image.fromarray(np.round(arr).astype(np.uint8), "RGB")
        frames.append(img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    return frames


MODELS = ("gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-flash-lite-latest")


def repair(system, prompt, code, error):
    """Ask Gemini to fix code that failed here (same loop as the legacy PC engine). Returns new code or ''."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key or not system or not prompt:
        return ""
    text = prompt + "\n\nOnceki kod:\n" + code + "\n\nBu kod calisirken hata verdi, hatayi duzeltip kodun tamamini yeniden yaz:\n" + error
    body = json.dumps({"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": text}]}]}).encode()
    first = str(payload.get("model", ""))
    order = ([first] if re.fullmatch(r"[a-z0-9.-]+", first) else []) + [m for m in MODELS if m != first]
    for model in order:           # the model that just worked for the worker is the one with quota left
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model, data=body,
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print("repair model %s unavailable (HTTP %d)" % (model, e.code))
            continue
        except Exception as e:
            print("repair model %s failed (%s)" % (model, type(e).__name__))
            continue
        out_text = "\n".join(p.get("text", "") for p in data.get("candidates", [{}])[0].get("content", {}).get("parts", []))
        m = re.search(r"```(?:python)?\s*(.*?)```", out_text, re.S)
        fixed = (m.group(1) if m else out_text).strip()
        if "def frame" in fixed:
            print("repaired with", model)
            return fixed
    return ""


try:
    code = payload.get("code", "")
    if not isinstance(code, str) or len(code) > 48000:
        raise ValueError("invalid generated code")
    w, h = int(payload.get("w", 0)), int(payload.get("h", 0))
    if (w, h) not in ((104, 52), (52, 104)):
        raise ValueError("unsupported panel dimensions")
    system, prompt = str(payload.get("system", ""))[:6000], str(payload.get("prompt", ""))[:400]
    frames, attempt = None, 0
    for attempt in range(1, 4):
        try:
            frames = render_frames(code, w, h)
            break
        except ValueError as err:
            print("attempt %d failed: %s" % (attempt, str(err).splitlines()[-1][:200]))
            fixed = repair(system, prompt, code, str(err)) if attempt < 3 else ""
            if not fixed:
                raise
            code = fixed
    target = out / effect_id
    frames[0].save(target, save_all=True, append_images=frames[1:], duration=FRAME_MS, loop=0, optimize=False, disposal=2)
    if target.stat().st_size > 12_000_000:
        target.unlink(missing_ok=True)
        raise ValueError("rendered animation exceeds the library size limit")
    print(f"Rendered {len(frames)} frames at {w}x{h} (attempt {attempt}): {target.stat().st_size} bytes")
except Exception as exc:
    (out / (effect_id[:-4] + ".gif")).unlink(missing_ok=True)
    error_path.write_text(json.dumps({"error": str(exc)[-700:]}), encoding="utf-8")
    print("Animation generation failed:", str(exc)[-700:])
