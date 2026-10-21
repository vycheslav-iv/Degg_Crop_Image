"""Проверка на РЕАЛЬНОМ воркфлоу «Test Crop»: реальная картинка 2720×1536 и
сохранённый crop_rect. Ожидание: размер результата считается по пикселям кадра
(16:9 → 1928×1088), а не по долям (1448×1448).

Запуск: python tests/_check_real_workflow.py
"""

from __future__ import annotations

import asyncio
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

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
IMAGE = "image_flux3_klein_distilled_t2i_00023_.png"
CROP_RECT = {"x": 0.181794, "y": 0.183037, "w": 0.636412, "h": 0.633926}


async def main() -> int:
    import subprocess
    import tempfile

    port = 9345
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
                page = next((p for p in pages if p.get("type") == "page" and p.get("webSocketDebuggerUrl")), None)
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
                res = await call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
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

            info = await evaluate(
                "(() => { const g = app.graph;"
                " const load = LiteGraph.createNode('LoadImage');"
                " g.add(load);"
                " const w = load.widgets.find((x) => x.name === 'image');"
                " w.value = '%s';"
                " load.imgs = [{ src: window.comfyAPI.api.api.apiURL('/view?filename=' + encodeURIComponent('%s') + '&type=input') }];"
                " const crop = LiteGraph.createNode('DeggCropImage');"
                " g.add(crop); crop.setSize([460, 460]);"
                " load.connect(0, crop, 0);"
                " window.__probeLoad = load; window.__probeCrop = crop;"
                " return true; })()" % (IMAGE, IMAGE)
            )
            await asyncio.sleep(3.0)

            # Проверяем, что картинка реально неквадратная: иначе смысла в
            # этой проверке нет (доли и пиксели совпадают — ошибка невидима).
            natural = await evaluate(
                "(() => { const i = window.__probeCrop.__deggCrop.widget"
                ".querySelector('.degg-crop__img');"
                " return i ? [i.naturalWidth, i.naturalHeight] : [0, 0]; })()"
            ) or [0, 0]
            if not natural[0] or natural[0] == natural[1]:
                print(
                    "ПРОПУСК: картинка '%s' недоступна или квадратная — "
                    "проверка «размер по пикселям» на реальном воркфлоу "
                    "не выполнялась" % IMAGE
                )
                return 0

            out = await evaluate(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " const cr = (n.widgets || []).find((x) => x.name === 'crop_rect');"
                " if (cr) cr.value = %s;"
                " e.refresh();"
                " const s = e.state;"
                " const fw = (n.widgets || []).find((x) => x.name === 'frame_w');"
                " const fh = (n.widgets || []).find((x) => x.name === 'frame_h');"
                " return { badge: e.widget.querySelector('.degg-crop__badge').textContent,"
                "  w: fw ? fw.value : null, h: fh ? fh.value : null,"
                "  img: s.img, frac: s.frac }; })()" % json.dumps(json.dumps(CROP_RECT))
            )
            await asyncio.sleep(0.5)
            out2 = await evaluate(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " const fw = (n.widgets || []).find((x) => x.name === 'frame_w');"
                " const fh = (n.widgets || []).find((x) => x.name === 'frame_h');"
                " return { badge: e.widget.querySelector('.degg-crop__badge').textContent,"
                "  w: fw ? fw.value : null, h: fh ? fh.value : null, img: e.state.img }; })()"
            )
            print("источник:", out2.get("img"))
            print("плашка :", (out2.get("badge") or "").replace("\n", " | "))
            print("поля   :", out2.get("w"), "x", out2.get("h"))
            ok = (out2.get("w") == 1928 and out2.get("h") == 1088
                  and "1928 × 1088 px" in (out2.get("badge") or ""))
            print("ИТОГ:", "OK — размер по пикселям кадра" if ok else "СБОЙ: размер неверный")
            return 0 if ok else 1
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except Exception:
            chrome.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
