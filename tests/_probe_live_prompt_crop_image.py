"""Живая проба: что фронтенд кладёт в промпт для DeggCropImage.

Причина проверки: служебный виджет `image` (канал штатного Mask Editor)
обязан НЕ попадать в промпт. Виджеты сериализуются в промпт по ИМЕНИ, а у
ноды есть вход `image` (IMAGE) — строка из служебного виджета оказывается
значением входа, сервер считает источник поданным и отдаёт пустой результат
8×8.

Проба открывает ComfyUI в headless Chrome, создаёт ноду, выбирает файл из
папки input, забирает промпт через `app.graphToPrompt()` и проверяет:
  1. `image_file` — имя файла;
  2. `image` в промпте НЕ строка (нет провода — ключа быть не должно);
  3. после «покраски маски» (в служебный виджет image записан
     clipspace-painted-masked-…) `image` в промпте всё равно не строка.

Запуск (ComfyUI должен быть запущен):
    cd Degg_Crop_Image
    python tests/_probe_live_prompt_crop_image.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
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


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self.events: list[dict] = []

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
            if "method" in msg:
                self.events.append(msg)

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


SETUP_JS = r"""
(() => {
  const g = app.graph;
  // Фронтенд восстанавливает последний воркфлоу — начинаем с чистого графа,
  // иначе в промпт попадут чужие ноды (и их ошибки).
  g.clear();
  const crop = LiteGraph.createNode('DeggCropImage');
  crop.pos = [200, 200];
  g.add(crop);
  crop.setSize([600, 900]);
  const w = (name) => (crop.widgets || []).find((x) => x.name === name) || null;
  window.__probeCrop = crop;
  window.__probeWidget = w;
  const file = w('image_file');
  if (file) {
    file.value = 'example.png';
    file.callback?.('example.png', app.canvas, crop);
  }
  return {
    id: crop.id,
    order: (crop.widgets || []).map((x) => x.name),
    serializeOption: (w('image')?.options || {}).serialize,
    serializeProperty: w('image')?.serialize,
  };
})()
"""

PROMPT_JS = r"""
(async () => {
  const result = await app.graphToPrompt();
  const node = window.__probeCrop;
  const entry = result.output[node.id];
  const inputs = entry ? Object.assign({}, entry.inputs) : {};
  return {
    class_type: entry ? entry.class_type : null,
    image_file: inputs.image_file,
    image_type: Array.isArray(inputs.image) ? 'link' : typeof inputs.image,
    image_value: Array.isArray(inputs.image) ? inputs.image : String(inputs.image ?? ''),
    keys: Object.keys(inputs),
  };
})()
"""


END_TO_END_JS = r"""
(async () => {
  const crop = window.__probeCrop;
  const save = LiteGraph.createNode('SaveImage');
  save.pos = [1000, 200];
  app.graph.add(save);
  const prefix = (save.widgets || []).find((w) => w.name === 'filename_prefix');
  if (prefix) prefix.value = 'degg-crop-prompt-probe';
  crop.connect(0, save, 0);
  window.__probeSave = save;
  // Ровно то, что фронтенд кладёт в промпт после покраски маски.
  const w = window.__probeWidget('mask_file');
  w.value = 'clipspace-painted-masked-1792336605078.png [input]';
  const result = await app.graphToPrompt();
  return { prompt: result.output, crop_id: crop.id, save_id: save.id };
})()
"""


def run_prompt(payload: dict) -> tuple[int, int] | None:
    """Ставит промпт фронтенда в очередь и возвращает размер результата."""
    import io
    import time as _time
    import urllib.parse as _parse
    import urllib.request as _request
    import uuid as _uuid

    from PIL import Image

    prompt = payload.get("prompt") or {}
    save_id = str(payload.get("save_id"))
    body = json.dumps({"prompt": prompt, "client_id": _uuid.uuid4().hex}).encode("utf-8")
    req = _request.Request(
        args_server() + "/prompt", data=body, headers={"Content-Type": "application/json"}
    )
    with _request.urlopen(req, timeout=60) as resp:
        prompt_id = json.load(resp)["prompt_id"]
    print(f"→ очередь: prompt_id={prompt_id}")

    history = {}
    deadline = _time.time() + 300
    while _time.time() < deadline:
        _time.sleep(1.5)
        try:
            with _request.urlopen(args_server() + "/history/" + prompt_id, timeout=30) as resp:
                history = json.load(resp)
        except Exception:
            continue
        entry = history.get(prompt_id)
        if entry and entry.get("status", {}).get("completed") is not None:
            status = entry.get("status", {}).get("status_str")
            if status == "error":
                print(f"  ошибка выполнения: {entry.get('status', {}).get('messages')}")
                return None
            images = (entry.get("outputs", {}).get(save_id, {}) or {}).get("images") or []
            if images:
                item = images[0]
                params = _parse.urlencode(
                    {
                        "filename": item["filename"],
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    }
                )
                with _request.urlopen(args_server() + "/view?" + params, timeout=60) as resp:
                    data = resp.read()
                image = Image.open(io.BytesIO(data))
                return image.size
    return None


_SERVER_ARG = {"url": "http://127.0.0.1:8188"}


def args_server() -> str:
    return _SERVER_ARG["url"].rstrip("/")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188/")
    ap.add_argument("--port", type=int, default=9337)
    ap.add_argument("--keep", action="store_true", help="не удалять профиль Chrome")
    args = ap.parse_args()
    _SERVER_ARG["url"] = args.url

    import websockets  # зависимость ComfyUI

    profile = Path(tempfile.mkdtemp(prefix="degg-crop-prompt-"))
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
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            print("→ ждём фронтенд…")
            await wait_for(cdp, "!!(window.app && window.app.isGraphReady)", 180, "isGraphReady")
            await wait_for(cdp, "!!(window.LiteGraph && window.app.graph)", 60, "graph")
            setup = await cdp.eval(SETUP_JS)
            print(f"→ нода {setup.get('id')}: служебный виджет image "
                  f"serialize(options)={setup.get('serializeOption')!r}, "
                  f"serialize(property)={setup.get('serializeProperty')!r}")
            await asyncio.sleep(0.5)

            first = await cdp.eval(PROMPT_JS, await_promise=True)
            print(f"→ промпт без покраски: {first}")
            check("image_file попал в промпт именем файла",
                  first.get("image_file") == "example.png", first.get("image_file"))
            check("без провода `image` в промпте НЕ строка",
                  first.get("image_type") != "string", first.get("image_value"))

            # Ровно то, что делает штатный Mask Editor при сохранении:
            # writeImageWidgetValue(node, 'image', 'clipspace-painted-masked-… [input]').
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const w = window.__probeWidget('image');"
                " if (!w) return false; w.value = 'clipspace-painted-masked-1792507596383.png [input]';"
                " w.callback?.('clipspace-painted-masked-1792507596383.png [input]', app.canvas, n);"
                " return true; })()"
            )
            await asyncio.sleep(0.3)
            second = await cdp.eval(PROMPT_JS, await_promise=True)
            print(f"→ промпт после покраски: {second}")
            check("после покраски `image` в промпте НЕ строка",
                  second.get("image_type") != "string", second.get("image_value"))

            mask_widget = await cdp.eval(
                "(() => { const w = window.__probeWidget('mask_file'); return w ? w.value : null; })()"
            )
            check("покраска доехала до входа mask_file",
                  isinstance(mask_widget, str) and "clipspace-painted-masked-" in mask_widget,
                  mask_widget)

            # ── сквозной прогон: промпт ровно такой, каким его отдаёт фронтенд ──
            payload = await cdp.eval(END_TO_END_JS, await_promise=True)
            size = run_prompt(payload)
            print(f"→ фактический размер результата: {size}")
            check("результат не пустая заглушка 8×8", size is not None and size != (8, 8), size)
            check("результат — настоящий размер кадра", bool(size) and min(size) > 64, size)

            # ── страховка сервера: промпт СТАРОГО вида (строка во входе `image`) ──
            # Так выглядели промпты, пока служебный виджет сериализовался: фронтенд
            # клал в `image` имя файла вместо провода. Сервер обязан понять, что
            # это не картинка, и прочитать image_file, а не отдать пустые 8×8.
            legacy = json.loads(json.dumps(payload))
            legacy["prompt"][str(payload["crop_id"])]["inputs"]["image"] = "example.png"
            legacy_size = run_prompt(legacy)
            print(f"→ промпт со строкой в `image` (как у старых воркфлоу): {legacy_size}")
            check(
                "старый промпт со строкой в `image` даёт настоящий кадр, а не 8×8",
                legacy_size is not None and legacy_size != (8, 8),
                legacy_size,
            )
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
