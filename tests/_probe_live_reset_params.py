"""Живая проба: «Сбросить» и смена картинки = параметры ровно как у источника.

Задача пользователя (2026-10-22): кнопка «Сбросить», выбор файла и подключение
картинки проводом обязаны приводить ноду в точности к параметрам исходного
изображения (его пиксели + эквивалент мегапикселей), чтобы на выходе получалось
ТО ЖЕ изображение, если рамку/размер никто не менял.

Проба открывает ComfyUI в headless Chrome и меряет:
  1. файл 768×768: `crop_rect` = весь кадр, `output_size` = "768,768",
     мегапиксели = 768·768/1024², выход = 768×768 и пиксельно равен источнику;
  2. файл 1001×751 (стороны НЕ кратны 8): при multiplicity=0 (по умолчанию)
     параметры точные ("1001,751") и выход пиксельно равен источнику; подгонка
     под кратность — это п.5;
  3. «Сбросить» после ручной обрезки и ручного размера возвращает ровно
     параметры источника, выход снова равен источнику;
  4. источник проводом (LoadImage → DeggCropImage): нода принимает размер
     картинки, выход пиксельно равен источнику;
  5. кратность применяется и к размеру источника: при multiplicity=8 картинка
     1001×751 даёт 1000×752 (стороны кратны 8), при multiplicity=0 — снова
     ровно 1001×751 (кратность не «залипает», обратима).

Запуск (ComfyUI должен быть запущен):
    cd Degg_Crop_Image
    python tests/_probe_live_reset_params.py
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

NAME_768 = "probe_reset_768.png"
NAME_ODD = "probe_reset_1001x751.png"


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
    rng = np.random.default_rng(20261022)
    for name, (w, h) in ((NAME_768, (768, 768)), (NAME_ODD, (1001, 751))):
        path = out / name
        if path.exists():
            with Image.open(path) as im:
                if im.size == (w, h):
                    continue
        data = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
        Image.fromarray(data, "RGB").save(path)
    print(f"→ источники: {out / NAME_768}, {out / NAME_ODD}")


def load_png(path: Path):
    from PIL import Image

    with Image.open(path) as im:
        return im.convert("RGB").copy()


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


# ── JS: состояние ноды ──────────────────────────────────────────────────────

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

# Состояние, которое пользователь видит/настраивает, + превью.
STATE_JS = r"""
(() => {
  const n = window.__probeCrop;
  const v = (name) => { const w = (n.widgets || []).find((x) => x.name === name); return w ? w.value : null; };
  const img = document.querySelector('.degg-crop__img');
  const badge = document.querySelector('.degg-crop__badge');
  return {
    crop_rect: v('crop_rect'),
    output_size: v('output_size'),
    megapixels: v('megapixels'),
    frame_w: v('frame_w'),
    frame_h: v('frame_h'),
    multiplicity: v('multiplicity'),
    lock_ratio: v('lock_ratio'),
    aspect_ratio: v('aspect_ratio'),
    preview: img ? [img.naturalWidth, img.naturalHeight] : null,
    badge: badge ? badge.textContent : null,
    server_url: n ? undefined : undefined,
  };
})()
"""

PROMPT_JS = r"""
(async () => {
  const result = await app.graphToPrompt();
  return { prompt: result.output, crop_id: window.__probeCrop.id, save_id: window.__probeSave.id };
})()
"""

# Ручная правка: как будто пользователь обрезал рамку и ввёл размер.
MANGLE_JS = r"""
(() => {
  const n = window.__probeCrop;
  const w = (name) => (n.widgets || []).find((x) => x.name === name);
  const rect = w('crop_rect');
  rect.value = JSON.stringify({ x: 0.25, y: 0.25, w: 0.5, h: 0.5 });
  rect.callback?.(rect.value, app.canvas, n);
  const out = w('output_size');
  out.value = '512,512';
  out.callback?.('512,512', app.canvas, n);
  return { crop_rect: rect.value, output_size: out.value };
})()
"""

SET_MULT_JS = r"""
(() => {
  const n = window.__probeCrop;
  const w = (n.widgets || []).find((x) => x.name === 'multiplicity');
  if (!w) return null;
  w.value = __MULT__;
  w.callback?.(w.value, app.canvas, n);
  return w.value;
})()
"""

CLICK_RESET_JS = r"""
(() => {
  const bar = document.querySelector('.degg-crop__bar');
  if (!bar) return false;
  const buttons = bar.querySelectorAll('.degg-crop__btn');
  const reset = buttons[2];
  if (!reset) return false;
  reset.click();
  return true;
})()
"""


def run_prompt(payload: dict):
    """Ставит промпт фронтенда в очередь, возвращает PIL-картинку результата."""
    from PIL import Image

    prompt = payload.get("prompt") or {}
    save_id = str(payload.get("save_id"))
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
        body = exc.read().decode("utf-8", "replace")
        print(f"  сервер отклонил промпт ({exc.code}): {body[:2000]}")
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
        if not entry or entry.get("status", {}).get("completed") is None:
            continue
        if entry.get("status", {}).get("status_str") == "error":
            print(f"  ошибка выполнения: {entry.get('status', {}).get('messages')}")
            return None
        images = (entry.get("outputs", {}).get(save_id, {}) or {}).get("images") or []
        if not images:
            continue
        item = images[0]
        params = urllib.parse.urlencode(
            {
                "filename": item["filename"],
                "subfolder": item.get("subfolder", ""),
                "type": item.get("type", "output"),
            }
        )
        with urllib.request.urlopen(
            _SERVER["url"].rstrip("/") + "/view?" + params, timeout=60
        ) as resp:
            data = resp.read()
        image = Image.open(io.BytesIO(data))
        return image.convert("RGB").copy()
    return None


def identity(label: str, got, source, expect_size) -> None:
    import numpy as np

    check(f"{label}: размер {expect_size[0]}×{expect_size[1]}",
          got is not None and got.size == expect_size, None if got is None else got.size)
    if got is None or source is None:
        check(f"{label}: пиксели совпадают с источником", False, "нет результата")
        return
    a = np.asarray(got)
    b = np.asarray(source)
    same = a.shape == b.shape and bool((a == b).all())
    diff = None if a.shape != b.shape else int((a != b).any(axis=2).sum())
    check(f"{label}: пиксели совпадают с источником", same,
          None if same else f"различается пикселей: {diff}")


def params_check(label: str, state: dict, w: int, h: int, manual=None) -> None:
    """`w,h` — ИТОГОВЫЙ размер (кадр, бейдж); `manual` — что лежит в скрытом
    `output_size`. Спека (§9.2): там ЗАПРОШЕННЫЙ размер, кратность применяется
    при выводе, поэтому при multiplicity>0 эти числа расходятся."""
    rect = {}
    try:
        rect = json.loads(state.get("crop_rect") or "{}")
    except Exception:
        pass
    check(f"{label}: рамка = весь кадр",
          all(abs(float(rect.get(k, -1)) - v) < 1e-6 for k, v in
              (("x", 0.0), ("y", 0.0), ("w", 1.0), ("h", 1.0))), state.get("crop_rect"))
    size = str(state.get("output_size") or "")
    want_manual = f"{w},{h}" if manual is None else f"{manual[0]},{manual[1]}"
    check(f"{label}: output_size = {want_manual}", size == want_manual, size)
    mp = state.get("megapixels")
    want_mp = (w * h) / (1024.0 * 1024.0)
    check(f"{label}: мегапиксели = площадь источника ({want_mp:.4f})",
          isinstance(mp, (int, float)) and abs(float(mp) - want_mp) <= 0.01, mp)
    check(f"{label}: кадр в пикселях = {w}×{h}",
          state.get("frame_w") == w and state.get("frame_h") == h,
          f"{state.get('frame_w')}×{state.get('frame_h')}")
    badge = state.get("badge") or ""
    check(f"{label}: бейдж показывает {w} × {h}", f"{w} × {h}" in badge, badge)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188/")
    ap.add_argument("--port", type=int, default=9341)
    ap.add_argument("--input-dir", default=str(_INPUT["dir"]))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    _SERVER["url"] = args.url
    _INPUT["dir"] = Path(args.input_dir)

    prepare_sources()
    src768 = load_png(_INPUT["dir"] / NAME_768)
    src_odd = load_png(_INPUT["dir"] / NAME_ODD)

    import websockets

    profile = Path(tempfile.mkdtemp(prefix="degg-crop-reset-"))
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

            # ── 1. файл 768×768 ─────────────────────────────────────────────
            setup = await cdp.eval(SETUP_JS)
            print(f"→ нода {setup.get('crop')}")
            await cdp.eval(f"window.__probeSetFile({json.dumps(NAME_768)})")
            await wait_for(
                cdp,
                "(() => { const w = (window.__probeCrop.widgets || []).find(x => x.name === 'output_size');"
                " return !!(w && w.value); })()",
                60, "output_size после выбора файла",
            )
            await asyncio.sleep(0.5)
            state = await cdp.eval(STATE_JS)
            print(f"→ параметры после файла {NAME_768}: {state}")
            params_check("файл 768", state, 768, 768)

            payload = await cdp.eval(PROMPT_JS, await_promise=True)
            identity("файл 768 — выход", run_prompt(payload), src768, (768, 768))

            # ── 2. файл 1001×751 (стороны не кратны multiplicity) ───────────
            await cdp.eval(f"window.__probeSetFile({json.dumps(NAME_ODD)})")
            await wait_for(
                cdp,
                "(() => { const n = window.__probeCrop; const w = (n.widgets || []).find(x => x.name === 'output_size');"
                " const img = document.querySelector('.degg-crop__img');"
                " return !!(w && w.value && img && img.naturalWidth === 1001); })()",
                60, "output_size после файла 1001×751",
            )
            await asyncio.sleep(0.5)
            state = await cdp.eval(STATE_JS)
            print(f"→ параметры после файла {NAME_ODD}: {state}")
            params_check("файл 1001×751", state, 1001, 751)

            payload = await cdp.eval(PROMPT_JS, await_promise=True)
            identity("файл 1001×751 — выход", run_prompt(payload), src_odd, (1001, 751))

            # ── 3. «Сбросить» после ручной обрезки и ручного размера ────────
            mangled = await cdp.eval(MANGLE_JS)
            print(f"→ руками испорчено: {mangled}")
            await asyncio.sleep(0.3)
            clicked = await cdp.eval(CLICK_RESET_JS)
            check("кнопка «Сбросить» найдена и нажата", clicked)
            await asyncio.sleep(0.6)
            state = await cdp.eval(STATE_JS)
            print(f"→ параметры после «Сбросить»: {state}")
            params_check("«Сбросить»", state, 1001, 751)

            payload = await cdp.eval(PROMPT_JS, await_promise=True)
            identity("«Сбросить» — выход", run_prompt(payload), src_odd, (1001, 751))

            # ── 4. источник проводом (LoadImage) ───────────────────────────
            wire = await cdp.eval(WIRE_JS.replace("__FILE__", NAME_768))
            payload = await cdp.eval(PROMPT_JS, await_promise=True)
            run_prompt(payload)
            # LoadImage отдаёт превью только после выполнения — ждём, пока нода
            # примет размер картинки (пробуждение watchSource).
            await wait_for(
                cdp,
                "(() => { const n = window.__probeCrop; const w = (n.widgets || []).find(x => x.name === 'output_size');"
                " return !!(w && w.value); })()",
                90, "output_size после подключения провода",
            )
            await asyncio.sleep(0.5)
            state = await cdp.eval(STATE_JS)
            print(f"→ параметры при источнике проводом: {state} (слот {wire.get('inSlot')})")
            params_check("провод", state, 768, 768)

            payload = await cdp.eval(PROMPT_JS, await_promise=True)
            identity("провод — выход", run_prompt(payload), src768, (768, 768))

            # ── 5. кратность применяется к размеру источника (жалоба 2026-10-29) ──
            # Пользователь: «Кратность вообще не работает, если картинка
            # загружена файлом или подключена — работает только в режиме кропа».
            # Ручной размер (его пишут загрузка и «Сбросить») обязан проходить
            # через кратность так же, как мегапиксельный.
            # Новая нода в режиме файла: __probeSetFile из секции 1 смотрел бы
            # на узел, который g.clear() уже отсоединил от графа (node.graph
            # null → NullGraphError из getInputNode).
            setup_f = await cdp.eval(SETUP_JS)
            print(f"→ нода для кратности: {setup_f.get('crop')}")
            await cdp.eval(f"window.__probeSetFile({json.dumps(NAME_ODD)})")
            await wait_for(
                cdp,
                "(() => { const n = window.__probeCrop; const w = (n.widgets || []).find(x => x.name === 'output_size');"
                " const img = document.querySelector('.degg-crop__img');"
                " return !!(w && w.value && img && img.naturalWidth === 1001); })()",
                90, "output_size после повторной загрузки 1001×751",
            )
            await asyncio.sleep(0.5)
            mult0 = await cdp.eval(SET_MULT_JS.replace("__MULT__", "0"))
            await asyncio.sleep(0.4)
            state = await cdp.eval(STATE_JS)
            print(f"→ кратность 0: {state}")
            check("кратность 0 принята виджетом", mult0 == 0, mult0)
            params_check("кратность 0", state, 1001, 751)
            payload = await cdp.eval(PROMPT_JS, await_promise=True)
            identity("кратность 0 — выход", run_prompt(payload), src_odd, (1001, 751))

            mult8 = await cdp.eval(SET_MULT_JS.replace("__MULT__", "8"))
            await asyncio.sleep(0.4)
            state = await cdp.eval(STATE_JS)
            print(f"→ кратность 8: {state}")
            check("кратность 8 принята виджетом", mult8 == 8, mult8)
            params_check("кратность 8", state, 1000, 752, manual=(1001, 751))
            payload = await cdp.eval(PROMPT_JS, await_promise=True)
            got = run_prompt(payload)
            check("кратность 8 — выход 1000×752 (стороны кратны 8)",
                  got is not None and got.size == (1000, 752),
                  None if got is None else got.size)

            mult_off = await cdp.eval(SET_MULT_JS.replace("__MULT__", "0"))
            await asyncio.sleep(0.4)
            state = await cdp.eval(STATE_JS)
            print(f"→ кратность снова 0: {state} (виджет {mult_off})")
            params_check("кратность снова 0", state, 1001, 751)
            payload = await cdp.eval(PROMPT_JS, await_promise=True)
            identity("кратность снова 0 — выход", run_prompt(payload), src_odd, (1001, 751))
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
