"""Проверка на РЕАЛЬНОМ воркфлое «Test Crop»: реальная картинка 2720×1536 и
сохранённый crop_rect. Ожидание: размер результата считается по пикселям кадра
(16:9 → 1928×1088 при 2 МП), а не по долям (1448×1448).

Сценарий — как открытие воркфлоу: сначала нода получает сохранённое состояние
(виджеты + onConfigure), потом восстанавливаются связи.

Запуск: python tests/_check_real_workflow.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import websockets  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import crop_image as ci  # тот же расчёт, что и на сервере

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
IMAGE = "image_flux3_klein_distilled_t2i_00023_.png"
CROP_RECT = {"x": 0.181794, "y": 0.183037, "w": 0.636412, "h": 0.633926}
MULTIPLICITY = 8
MEGAPIXELS = 2.0

SETUP_JS = """
(() => {
  const g = app.graph;
  const load = LiteGraph.createNode('LoadImage');
  g.add(load);
  const w = load.widgets.find((x) => x.name === 'image');
  w.value = '__IMAGE__';
  load.imgs = [{
    src: window.comfyAPI.api.api.apiURL(
      '/view?filename=' + encodeURIComponent('__IMAGE__') + '&type=input'),
  }];
  const crop = LiteGraph.createNode('DeggCropImage');
  g.add(crop);
  crop.setSize([460, 460]);
  window.__probeLoad = load;
  window.__probeCrop = crop;
  // Сохранённое состояние ноды: рамка, пропорции, мегапиксели, кратность.
  const set = (name, value) => {
    const w2 = (crop.widgets || []).find((x) => x.name === name);
    if (w2) w2.value = value;
  };
  set('crop_rect', '__RECT__');
  set('aspect_ratio', '16:9');
  set('megapixels', __MP__);
  set('multiplicity', __MULT__);
  try { crop.onConfigure(); } catch (e) { /* onConfigure может требовать info */ }
  // Связи восстанавливаются ПОСЛЕ onConfigure — как в воркфлое.
  load.connect(0, crop, 0);
  return true;
})()
""".replace("__IMAGE__", IMAGE).replace("__RECT__", json.dumps(CROP_RECT)).replace(
    "__MP__", str(MEGAPIXELS)
).replace("__MULT__", str(MULTIPLICITY))

STATE_JS = """
(() => {
  const n = window.__probeCrop;
  const e = n && n.__deggCrop;
  if (!e) return { mounted: false };
  const q = (sel) => e.widget.querySelector(sel);
  const img = q('.degg-crop__img');
  const badge = q('.degg-crop__badge');
  const fw = (n.widgets || []).find((x) => x.name === 'frame_w');
  const fh = (n.widgets || []).find((x) => x.name === 'frame_h');
  return {
    mounted: true,
    badge: badge ? badge.textContent : '',
    w: fw ? fw.value : null,
    h: fh ? fh.value : null,
    img: e.state.img,
    frac: e.state.frac,
  };
})()
"""


async def main() -> int:
    import subprocess
    import tempfile

    port = 9346
    profile = tempfile.mkdtemp(prefix="degg-real-")
    chrome = subprocess.Popen(
        [
            CHROME, "--headless=new", f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}", "--window-size=900,1200",
            "--no-first-run", "--disable-gpu", "--hide-scrollbars",
            "http://127.0.0.1:8188/",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        page = None
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                    pages = json.load(r)
                page = next(
                    (p for p in pages if p.get("type") == "page" and p.get("webSocketDebuggerUrl")),
                    None,
                )
                if page:
                    break
            except Exception:
                pass
            time.sleep(0.4)
        if not page:
            print("CDP не отвечает")
            return 2

        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=40 * 1024 * 1024) as ws:
            counter = {"id": 0}

            async def call(method, params=None):
                counter["id"] += 1
                mid = counter["id"]
                await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == mid:
                        if "error" in msg:
                            raise RuntimeError(f"{method}: {msg['error']}")
                        return msg.get("result", {})

            async def evaluate(expr):
                res = await call(
                    "Runtime.evaluate",
                    {"expression": expr, "returnByValue": True, "awaitPromise": True},
                )
                if "exceptionDetails" in res:
                    raise RuntimeError(str(res["exceptionDetails"])[:300])
                return res.get("result", {}).get("value")

            await call("Page.enable")
            await call("Runtime.enable")
            deadline = time.time() + 120
            while time.time() < deadline:
                try:
                    if await evaluate("!!(window.app && window.app.isGraphReady)"):
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
            await asyncio.sleep(2)

            await evaluate(SETUP_JS)
            await asyncio.sleep(3.5)

            state = await evaluate(STATE_JS) or {}
            raw_img = state.get("img")
            if isinstance(raw_img, dict):
                natural = [raw_img.get("w", 0), raw_img.get("h", 0)]
            elif isinstance(raw_img, (list, tuple)):
                natural = list(raw_img)
            else:
                natural = [0, 0]
            if not natural[0] or natural[0] == natural[1]:
                print(
                    "ПРОПУСК: картинка '%s' недоступна или квадратная — проверка "
                    "«размер по пикселям» на реальном воркфлоу не выполнялась" % IMAGE
                )
                return 0

            frac = state.get("frac") or {}
            # Ожидаемый размер считает PYTHON — тем же кодом, что и сервер:
            # пропорции кадра В ПИКСЕЛЯХ × мегапиксели.
            exp_w, exp_h = ci.output_size(
                frac.get("w", 0) * natural[0],
                frac.get("h", 0) * natural[1],
                MEGAPIXELS,
                MULTIPLICITY,
            )
            print("источник:", natural)
            print("рамка   :", frac)
            print("плашка  :", (state.get("badge") or "").replace("\n", " | "))
            print("поля    :", state.get("w"), "x", state.get("h"))
            ok = (
                state.get("w") == exp_w
                and state.get("h") == exp_h
                and f"{exp_w} × {exp_h} px" in (state.get("badge") or "")
            )
            print(
                "ИТОГ: %s (ожидалось %d × %d)"
                % ("OK — размер по пикселям кадра" if ok else "СБОЙ: размер неверный", exp_w, exp_h)
            )
            return 0 if ok else 1
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except Exception:
            chrome.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
