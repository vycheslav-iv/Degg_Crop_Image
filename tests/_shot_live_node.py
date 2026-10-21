"""Снимок живого узла DeggCropImage в браузере (headless Chrome + CDP).

Нужен, чтобы увидеть глазами то, что показывают замеры: где стоят поля
координат, где «?», как выглядят нативные опции.

Запуск (ComfyUI должен быть запущен):
    cd Degg_Crop_Image
    python tests/_shot_live_node.py
    python tests/_shot_live_node.py --out shot.png
"""

from __future__ import annotations

import argparse
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

try:
    import websockets
except Exception:  # pragma: no cover
    print("нужен websockets (есть в ComfyUI)", file=sys.stderr)
    raise

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ROOT = Path(__file__).resolve().parent.parent


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8188/")
    parser.add_argument("--out", default="node_shot.png")
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=1100)
    args = parser.parse_args()

    import subprocess
    import tempfile

    port = 9333
    profile = tempfile.mkdtemp(prefix="degg-shot-")
    chrome = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--window-size=900,1200",
            "--no-first-run",
            "--disable-gpu",
            "--hide-scrollbars",
            args.url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        page = None
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as resp:
                    pages = json.load(resp)
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
                    raise RuntimeError(str(res["exceptionDetails"])[:400])
                return res.get("result", {}).get("value")

            await call("Page.enable")
            await call("Runtime.enable")
            await call(
                "Emulation.setDeviceMetricsOverride",
                {"width": args.width, "height": args.height, "deviceScaleFactor": 1, "mobile": False},
            )
            await asyncio.sleep(4)
            deadline = time.time() + 120
            while time.time() < deadline:
                try:
                    if await evaluate("!!(window.app && window.app.isGraphReady)"):
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
            await asyncio.sleep(2)

            setup = """
            (() => {
              const g = app.graph;
              const load = LiteGraph.createNode('LoadImage');
              load.pos = [80, 120];
              g.add(load);
              load.title = 'upstream';
              const imageWidget = (load.widgets || []).find((w) => w.name === 'image');
              if (imageWidget) imageWidget.value = 'example.png';
              load.imgs = [{ src: window.comfyAPI.api.api.apiURL('/view?filename=' + encodeURIComponent('example.png') + '&type=input') }];

              const crop = LiteGraph.createNode('DeggCropImage');
              crop.pos = [560, 120];
              g.add(crop);
              crop.setSize([460, 460]);
              window.__probeLoad = load;
              window.__probeCrop = crop;
              load.connect(0, crop, 0);
              app.canvas.selectNodes([crop]);
              app.canvas.centerOnNode(crop);
              app.canvas.setZoom(1);
              app.canvas.setDirty(true, true);
              return { load: load.id, crop: crop.id, size: crop.size.slice() };
            })()
            """
            info = await evaluate(setup)
            deadline = time.time() + 30
            while time.time() < deadline:
                loaded = await evaluate(
                    "(() => { const n = window.__probeCrop; const e = n.__deggCrop;"
                    " const img = e.widget.querySelector('.degg-crop__img');"
                    " return !!(img && img.naturalWidth); })()"
                )
                if loaded:
                    break
                await asyncio.sleep(0.6)
            await asyncio.sleep(1.5)
            # Сценарий пользователя: ручной размер 1000 × 1000, затем выбор
            # 16:9 и 2 МП — устаревший ручной размер не должен перебивать их.
            await evaluate(
                "(() => { const crop = window.__probeCrop;"
                " const w = crop.widgets.find((x) => x.name === 'frame_w');"
                " const h = crop.widgets.find((x) => x.name === 'frame_h');"
                " if (!w || !h) return false;"
                " w.value = 1000; w.callback && w.callback(1000, app.canvas, crop);"
                " h.value = 1000; h.callback && h.callback(1000, app.canvas, crop);"
                " return true; })()"
            )
            await asyncio.sleep(0.6)
            await evaluate(
                "(() => { const crop = window.__probeCrop;"
                " const a = crop.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = '16:9'; a.callback && a.callback(a.value);"
                " const m = crop.widgets.find((x) => x.name === 'megapixels');"
                " m.value = 2.0; m.callback && m.callback(2.0, app.canvas, crop);"
                " return true; })()"
            )
            await asyncio.sleep(0.8)
            await evaluate("app.canvas.setDirty(true, true)")
            await asyncio.sleep(0.8)

            shot = await call("Page.captureScreenshot", {"format": "png"})
            out = Path(args.out)
            out.write_bytes(base64.b64decode(shot["data"]))
            print("saved:", out.resolve())
            print("widgets:", info)
            return 0
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except Exception:
            chrome.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
