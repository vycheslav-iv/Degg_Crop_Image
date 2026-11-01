"""Живая проба: переключение воркфлоу НЕ сбрасывает настройки ноды.

Жалоба пользователя (2026-11-01): «переключился на другой воркфлоу и вернулся —
настройки ноды сброшены, как будто нажал "Сбросить"». Сброс обязан происходить
ТОЛЬКО по кнопке «Сбросить», при вставке другой картинки файлом и при
подключении другой картинки проводом.

Прежняя проверка восстановления (`_probe_live_dom_crop_image.py`) была слепой:
она вызывала `n2.onConfigure()` вручную и подключала провод ПОСЛЕ, то есть
никогда не проходила настоящий путь `graph.configure()` + `loadedGraphNode`.

Что меряет эта проба (настоящий путь: serialize → другой граф → serialize назад):

  1. файл: после правки рамки (0.25/0.25/0.5/0.5) и размера (512,512)
     переключение воркфлоу туда-обратно сохраняет `crop_rect`, `output_size`,
     мегапиксели, `frame_w/frame_h` и состояние редактора (`state.frac`);
  2. провод (LoadImage → нода): то же самое после реального выполнения источника;
  3. вставка ДРУГОЙ картинки всё ещё сбрасывает параметры к источнику —
     исправление не должно отключить сброс совсем.

Запуск (ComfyUI должен быть запущен):
    cd Degg_Crop_Image
    python tests/_probe_live_workflow_switch.py
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

FAILS: list[str] = []
OKS = 0

_SERVER = {"url": "http://127.0.0.1:8188"}
_INPUT = {"dir": Path(r"D:/ComfyUI_windows_portable/ComfyUI/input")}

NAME_768 = "probe_switch_768.png"
NAME_ODD = "probe_switch_1001x751.png"

EDIT_RECT = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}
EDIT_SIZE = {"w": 512, "h": 512}


def check(label: str, cond, detail="") -> None:
    global OKS
    if cond:
        OKS += 1
        print(f"  ok    {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def find_chrome() -> str:
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).exists():
        return env
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    raise SystemExit("Chrome не найден (задай CHROME_PATH)")


def prepare_sources() -> None:
    """Детерминированные источники: шум (любое пересэмплирование видно)."""
    import numpy as np
    from PIL import Image

    out = _INPUT["dir"]
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20261101)
    for name, (w, h) in ((NAME_768, (768, 768)), (NAME_ODD, (1001, 751))):
        path = out / name
        if path.exists():
            with Image.open(path) as im:
                if im.size == (w, h):
                    continue
        data = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
        Image.fromarray(data, "RGB").save(path)
    print(f"→ источники: {out / NAME_768}, {out / NAME_ODD}")


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    async def call(self, method: str, params: dict | None = None):
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def eval(self, expr: str, await_promise: bool = False):
        res = await self.call(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": await_promise},
        )
        if "exceptionDetails" in res:
            raise RuntimeError(
                res["exceptionDetails"].get("exception", {}).get("description", "eval error")
            )
        return res.get("result", {}).get("value")


async def wait_for(cdp: CDP, expr: str, timeout: float = 60.0, label: str = ""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = await cdp.eval(expr)
            if last:
                return last
        except Exception:
            pass
        await asyncio.sleep(0.15)
    raise SystemExit(f"не дождались: {label or expr} (последнее: {last!r})")


def wait_devtools(port: int, timeout: float = 30.0) -> dict:
    url = f"http://127.0.0.1:{port}/json/list"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                pages = json.load(resp)
            for page in pages:
                if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                    return page
        except Exception:
            pass
        time.sleep(0.3)
    raise SystemExit("CDP не отвечает: страница не найдена")


SETUP_JS = r"""
(() => {
  const g = app.graph;
  g.clear();
  const crop = LiteGraph.createNode('DeggCropImage');
  crop.pos = [200, 200];
  g.add(crop);
  crop.setSize([600, 900]);
  const save = LiteGraph.createNode('SaveImage');
  save.pos = [1000, 200];
  g.add(save);
  window.__probeCrop = crop;
  window.__probeSave = save;
  crop.connect(0, save, 0);
  window.__probeSetFile = (name) => {
    const w = (crop.widgets || []).find((x) => x.name === 'image_file');
    if (!w) return false;
    w.value = name;
    w.callback?.(name, app.canvas, crop);
    return true;
  };
  return { crop: crop.id, save: save.id, order: (crop.widgets || []).map((x) => x.name) };
})()
"""

WIRE_JS = r"""
(() => {
  const g = app.graph;
  g.clear();
  const load = LiteGraph.createNode('LoadImage');
  load.pos = [0, 200];
  g.add(load);
  const crop = LiteGraph.createNode('DeggCropImage');
  crop.pos = [400, 200];
  g.add(crop);
  crop.setSize([600, 900]);
  const save = LiteGraph.createNode('SaveImage');
  save.pos = [1100, 200];
  g.add(save);
  const lw = (load.widgets || []).find((w) => w.name === 'image');
  if (lw) { lw.value = '__FILE__'; lw.callback?.('__FILE__', app.canvas, load); }
  const inSlot = (crop.inputs || []).findIndex((i) => i.name === 'image');
  load.connect(0, crop, inSlot);
  crop.connect(0, save, 0);
  window.__probeCrop = crop;
  window.__probeLoad = load;
  window.__probeSave = save;
  return { crop: crop.id, load: load.id, save: save.id, inSlot };
})()
"""

# Правка КАК У ПОЛЬЗОВАТЕЛЯ: двигаем рамку и вводим размер результата.
EDIT_JS = r"""
(() => {
  const n = window.__probeCrop;
  const e = n.__deggCrop;
  e.state.frac = { x: __RX__, y: __RY__, w: __RW__, h: __RH__ };
  e.state.manual = { w: __MW__, h: __MH__ };
  e.commit();
  return true;
})()
"""

STATE_JS = r"""
(() => {
  const n = window.__probeCrop;
  const v = (name) => { const w = (n.widgets || []).find((x) => x.name === name); return w ? w.value : null; };
  const img = document.querySelector('.degg-crop__img');
  const badge = document.querySelector('.degg-crop__badge');
  const e = n.__deggCrop;
  return {
    crop_rect: v('crop_rect'),
    output_size: v('output_size'),
    megapixels: v('megapixels'),
    frame_w: v('frame_w'),
    frame_h: v('frame_h'),
    multiplicity: v('multiplicity'),
    preview: img ? [img.naturalWidth, img.naturalHeight] : null,
    badge: badge ? badge.textContent : null,
    frac: e ? { ...e.state.frac } : null,
    restoring: e ? e.state.restoring : null,
  };
})()
"""

# Настоящее переключение воркфлоу: serialize → пустой граф → serialize назад.
SWITCH_JS = r"""
(async () => {
  const saved = JSON.parse(JSON.stringify(app.graph.serialize()));
  window.__probeSavedGraph = saved;
  const empty = { last_node_id: 0, last_link_id: 0, nodes: [], links: [],
                  groups: [], config: {}, extra: {}, version: 0.4 };
  await app.loadGraphData(JSON.parse(JSON.stringify(empty)));
  await new Promise((r) => setTimeout(r, 250));
  await app.loadGraphData(window.__probeSavedGraph);
  const crop = app.graph.getNodeById(__CROP_ID__);
  const load = __LOAD_ID__ === null ? null : app.graph.getNodeById(__LOAD_ID__);
  window.__probeCrop = crop;
  if (load) window.__probeLoad = load;
  return { nodes: (app.graph.nodes || []).length, crop: crop ? crop.id : null,
           load: load ? load.id : null };
})()
"""

PROMPT_JS = r"""
(async () => {
  const result = await app.graphToPrompt();
  return { prompt: result.output, crop_id: window.__probeCrop.id, save_id: window.__probeSave.id };
})()
"""

SET_FILE_JS = r"""
((name) => {
  const n = window.__probeCrop;
  const w = (n.widgets || []).find((x) => x.name === 'image_file');
  if (!w) return false;
  w.value = name;
  w.callback?.(name, app.canvas, n);
  return true;
})(__FILE__)
"""


def run_prompt(payload: dict):
    prompt = payload.get("prompt") or {}
    body = json.dumps({"prompt": prompt, "client_id": uuid.uuid4().hex}).encode("utf-8")
    req = urllib.request.Request(
        _SERVER["url"].rstrip("/") + "/prompt",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            prompt_id = json.load(resp)["prompt_id"]
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace")
        print(f"  сервер отклонил промпт ({exc.code}): {body_text[:2000]}")
        return None

    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(1.0)
        try:
            with urllib.request.urlopen(
                _SERVER["url"].rstrip("/") + "/history/" + prompt_id, timeout=30
            ) as resp:
                history = json.load(resp)
        except Exception:
            continue
        entry = history.get(prompt_id)
        if not entry:
            continue
        if entry.get("status", {}).get("status_str") == "error":
            print(f"  ошибка выполнения: {entry.get('status', {}).get('messages')}")
            return None
        if entry.get("status", {}).get("completed") is None:
            continue
        if (entry.get("outputs") or {}):
            return True
    return None


def rect_of(state: dict) -> dict:
    try:
        return json.loads(state.get("crop_rect") or "{}")
    except Exception:
        return {}


def same_rect(got: dict, want: dict) -> bool:
    try:
        return all(abs(float(got.get(k)) - float(want[k])) < 1e-6 for k in ("x", "y", "w", "h"))
    except Exception:
        return False


def compare_kept(label: str, before: dict, after: dict) -> None:
    check(f"{label}: превью загружено после возврата",
          bool(after.get("preview")), after.get("preview"))
    check(f"{label}: crop_rect не изменился",
          same_rect(rect_of(after), EDIT_RECT), after.get("crop_rect"))
    check(f"{label}: output_size не изменился",
          after.get("output_size") == f"{EDIT_SIZE['w']},{EDIT_SIZE['h']}",
          after.get("output_size"))
    check(f"{label}: мегапиксели не изменились",
          isinstance(after.get("megapixels"), (int, float))
          and abs(float(after["megapixels"]) - before["megapixels"]) <= 0.01,
          f"{before.get('megapixels')} → {after.get('megapixels')}")
    check(f"{label}: frame_w/frame_h не изменились",
          after.get("frame_w") == EDIT_SIZE["w"] and after.get("frame_h") == EDIT_SIZE["h"],
          f"{after.get('frame_w')}×{after.get('frame_h')}")
    check(f"{label}: рамка редактора не изменилась",
          isinstance(after.get("frac"), dict) and same_rect(after["frac"], EDIT_RECT),
          after.get("frac"))
    badge = after.get("badge") or ""
    check(f"{label}: бейдж показывает {EDIT_SIZE['w']} × {EDIT_SIZE['h']}",
          f"{EDIT_SIZE['w']} × {EDIT_SIZE['h']}" in badge, badge)


async def switch_away_and_back(cdp: CDP, crop_id: int, load_id) -> None:
    js = (
        SWITCH_JS.replace("__CROP_ID__", str(crop_id))
        .replace("__LOAD_ID__", "null" if load_id is None else str(load_id))
    )
    result = await cdp.eval(js, await_promise=True)
    print(f"→ после переключения: {result}")
    if not result or not result.get("crop"):
        raise SystemExit("нода DeggCropImage не восстановилась после переключения")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188/")
    ap.add_argument("--port", type=int, default=9343)
    ap.add_argument("--input-dir", default=str(_INPUT["dir"]))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    _SERVER["url"] = args.url
    _INPUT["dir"] = Path(args.input_dir)

    prepare_sources()

    import websockets

    profile = Path(tempfile.mkdtemp(prefix="degg-crop-switch-"))
    chrome = subprocess.Popen(
        [
            find_chrome(),
            "--headless=new",
            f"--remote-debugging-port={args.port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1400,1000",
            "--disable-features=Translate",
            args.url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        page = wait_devtools(args.port)
        async with websockets.connect(
            page["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024
        ) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            print("→ ждём фронтенд…")
            await wait_for(cdp, "!!(window.app && window.app.isGraphReady)", 180, "isGraphReady")
            await wait_for(cdp, "!!(window.LiteGraph && window.app.graph)", 60, "graph")

            # ── 1. файл: правка → переключение воркфлоу → возврат ───────────
            setup = await cdp.eval(SETUP_JS)
            print(f"→ нода {setup.get('crop')}")
            await cdp.eval(f"window.__probeSetFile({json.dumps(NAME_768)})")
            await wait_for(
                cdp,
                "(() => { const n = window.__probeCrop;"
                " const w = (n.widgets || []).find(x => x.name === 'output_size');"
                " const img = document.querySelector('.degg-crop__img');"
                " return !!(w && w.value && img && img.naturalWidth === 768); })()",
                60, "файл 768 загружен",
            )
            await asyncio.sleep(0.5)

            edit = (
                EDIT_JS.replace("__RX__", str(EDIT_RECT["x"]))
                .replace("__RY__", str(EDIT_RECT["y"]))
                .replace("__RW__", str(EDIT_RECT["w"]))
                .replace("__RH__", str(EDIT_RECT["h"]))
                .replace("__MW__", str(EDIT_SIZE["w"]))
                .replace("__MH__", str(EDIT_SIZE["h"]))
            )
            await cdp.eval(edit)
            await asyncio.sleep(0.5)
            before = await cdp.eval(STATE_JS)
            print(f"→ файл: до переключения {before}")
            check("файл: правка записалась (crop_rect)", same_rect(rect_of(before), EDIT_RECT),
                  before.get("crop_rect"))
            check("файл: правка записалась (output_size)",
                  before.get("output_size") == f"{EDIT_SIZE['w']},{EDIT_SIZE['h']}",
                  before.get("output_size"))

            await switch_away_and_back(cdp, setup.get("crop"), None)
            await wait_for(
                cdp,
                "(() => { const n = window.__probeCrop;"
                " const w = (n.widgets || []).find(x => x.name === 'output_size');"
                " const img = document.querySelector('.degg-crop__img');"
                " return !!(w && w.value && img && img.naturalWidth === 768); })()",
                60, "файл 768 после возврата",
            )
            await asyncio.sleep(1.2)
            after = await cdp.eval(STATE_JS)
            print(f"→ файл: после возврата {after}")
            compare_kept("файл", before, after)

            # ── 2. провод: правка → переключение → возврат ──────────────────
            wire = await cdp.eval(WIRE_JS.replace("__FILE__", NAME_768))
            payload = await cdp.eval(PROMPT_JS, await_promise=True)
            run_prompt(payload)
            await wait_for(
                cdp,
                "(() => { const n = window.__probeCrop;"
                " const w = (n.widgets || []).find(x => x.name === 'output_size');"
                " const img = document.querySelector('.degg-crop__img');"
                " return !!(w && w.value && img && img.naturalWidth === 768); })()",
                90, "провод: превью 768",
            )
            await asyncio.sleep(0.5)
            await cdp.eval(edit)
            await asyncio.sleep(0.5)
            before_w = await cdp.eval(STATE_JS)
            print(f"→ провод: до переключения {before_w}")
            check("провод: правка записалась (crop_rect)",
                  same_rect(rect_of(before_w), EDIT_RECT), before_w.get("crop_rect"))

            await switch_away_and_back(cdp, wire.get("crop"), wire.get("load"))
            await wait_for(
                cdp,
                "(() => { const n = window.__probeCrop;"
                " const w = (n.widgets || []).find(x => x.name === 'output_size');"
                " const img = document.querySelector('.degg-crop__img');"
                " return !!(w && w.value && img && img.naturalWidth === 768); })()",
                90, "провод: превью 768 после возврата",
            )
            await asyncio.sleep(1.2)
            after_w = await cdp.eval(STATE_JS)
            print(f"→ провод: после возврата {after_w}")
            compare_kept("провод", before_w, after_w)

            # ── 3. сброс должен ОСТАТЬСЯ: другая картинка файлом ────────────
            setup3 = await cdp.eval(SETUP_JS)
            await cdp.eval(f"window.__probeSetFile({json.dumps(NAME_768)})")
            await wait_for(
                cdp,
                "(() => { const n = window.__probeCrop;"
                " const w = (n.widgets || []).find(x => x.name === 'output_size');"
                " const img = document.querySelector('.degg-crop__img');"
                " return !!(w && w.value && img && img.naturalWidth === 768); })()",
                60, "сброс: файл 768 загружен",
            )
            await asyncio.sleep(0.4)
            await cdp.eval(edit)
            await asyncio.sleep(0.4)
            await cdp.eval(SET_FILE_JS.replace("__FILE__", json.dumps(NAME_ODD)))
            await wait_for(
                cdp,
                "(() => { const n = window.__probeCrop;"
                " const w = (n.widgets || []).find(x => x.name === 'output_size');"
                " const img = document.querySelector('.degg-crop__img');"
                " return !!(w && w.value && img && img.naturalWidth === 1001); })()",
                90, "сброс: файл 1001×751 загружен",
            )
            await asyncio.sleep(0.6)
            reset_state = await cdp.eval(STATE_JS)
            print(f"→ новая картинка: {reset_state}")
            check("новая картинка: рамка снова весь кадр",
                  same_rect(rect_of(reset_state), {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}),
                  reset_state.get("crop_rect"))
            check("новая картинка: output_size = 1001,751",
                  reset_state.get("output_size") == "1001,751", reset_state.get("output_size"))
    finally:
        if not args.keep:
            chrome.terminate()
            try:
                chrome.wait(timeout=10)
            except Exception:
                chrome.kill()

    print()
    if FAILS:
        print(f"КРАСНОЕ: провалов {len(FAILS)}, ок {OKS}")
        for name in FAILS:
            print(f"  - {name}")
        return 1
    print(f"ЗЕЛЁНОЕ: провалов 0, ок {OKS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
