"""A/B-диагностика раскладки DOM-виджета DeggCropImage.

Проверяет три гипотезы, почему высота DOM-виджета не следует за нодой:
  A) добавленные виджеты (выбор файла + кнопка загрузки) съедают место;
  B) переопределение widget.computeLayoutSize после первого рендера не даёт
     hasLayoutSize=true → строка грида остаётся 'min-content';
  C) без переопределения (getMinHeight в опциях) высота считается верно.
"""

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


def find_chrome() -> str:
    for path in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(path).exists():
            return path
    raise SystemExit("не найден Chrome/Edge")


def wait_devtools(port: int) -> dict:
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                pages = json.load(r)
            for page in pages:
                if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                    return page
        except Exception:
            pass
        time.sleep(0.4)
    raise SystemExit("DevTools не поднялся")


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.i = 0

    async def call(self, method, params=None):
        self.i += 1
        await self.ws.send(json.dumps({"id": self.i, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.i:
                return msg

    async def eval(self, expr):
        res = await self.call(
            "Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True}
        )
        if "exceptionDetails" in res.get("result", {}):
            raise RuntimeError(
                res["result"]["exceptionDetails"].get("exception", {}).get("description", "eval error")
            )
        return res.get("result", {}).get("result", {}).get("value")


DUMP = """(() => {
  const n = window.__diag;
  const w = (n.widgets || []).find((x) => x.name === 'preview');
  const cell = w && w.element && w.element.parentElement && w.element.parentElement.parentElement;
  const grid = cell ? cell.parentElement : null;
  const rows = grid ? Array.from(grid.children).map((row) => {
    const r = row.getBoundingClientRect();
    return { h: Math.round(r.height), cls: String(row.className || '').slice(0, 60) };
  }) : [];
  return {
    size: [n.size[0], n.size[1]],
    bodyHeight: n.bodyHeight,
    free: n.freeWidgetSpace,
    computed: w ? w.computedHeight : null,
    elH: w ? Math.round(w.element.getBoundingClientRect().height) : null,
    cellH: cell ? Math.round(cell.getBoundingClientRect().height) : null,
    gridH: grid ? Math.round(grid.getBoundingClientRect().height) : null,
    gridRows: grid ? getComputedStyle(grid).gridTemplateRows : null,
    rows,
    fileRow: (() => {
      const fw = (n.widgets || []).find((x) => x.name === 'image_file');
      if (!fw) return null;
      const el = fw.element || (fw.options && fw.options.el) || null;
      const row = el && el.closest ? (el.closest('[data-testid="node-widget"]') || el.parentElement) : null;
      return { hasEl: !!el, tag: el && el.tagName, html: row ? String(row.innerHTML).slice(0, 700) : null };
    })(),
    widgets: (n.widgets || []).length,
    names: (n.widgets || []).map((x) => x.name),
  };
})()"""


async def main() -> int:
    import websockets

    port = 9335
    profile = Path(tempfile.mkdtemp(prefix="degg-crop-diag2-"))
    chrome = subprocess.Popen(
        [
            find_chrome(),
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1600,1100",
            "http://127.0.0.1:8188/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        page = wait_devtools(port)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            deadline = time.time() + 120
            while time.time() < deadline:
                if await cdp.eval("!!(window.app && window.app.isGraphReady)"):
                    break
                await asyncio.sleep(0.5)
            await asyncio.sleep(1.0)

            await cdp.eval(
                """(() => {
  const g = app.graph;
  const crop = LiteGraph.createNode('DeggCropImage');
  crop.pos = [200, 200];
  g.add(crop);
  crop.setSize([600, 900]);
  window.__diag = crop;
  app.canvas.selectNodes([crop]);
  app.canvas.centerOnNode(crop);
  app.canvas.setZoom(1);
  app.canvas.setDirty(true, true);
  return crop.id;
})()"""
            )
            await asyncio.sleep(1.5)
            print("A) как есть:", json.dumps(await cdp.eval(DUMP), ensure_ascii=False))
            await cdp.eval("(() => { window.__diag.setSize([600, 1200]); return 1; })()")
            await asyncio.sleep(1.2)
            print("A) после resize 600x800:", json.dumps(await cdp.eval(DUMP), ensure_ascii=False))

            # B) убираем два новых виджета — влияет ли количество виджетов
            await cdp.eval(
                """(() => { const n = window.__diag;
  n.widgets = n.widgets.filter((w) => w.name !== 'image_file' && w.name !== 'upload');
  n.setDirtyCanvas(true, true);
  app.canvas.setDirty(true, true);
  return n.widgets.length; })()"""
            )
            await asyncio.sleep(1.2)
            print("B) без файловых виджетов:", json.dumps(await cdp.eval(DUMP), ensure_ascii=False))

            # C) проверяем, что виджет вообще умеет расти: задаём его
            #    computeLayoutSize так, чтобы минимум был 0, и смотрим
            print("E) DOM комбо-файла:", json.dumps(await cdp.eval(
                "(() => { const n = window.__diag;"
                " const w = (n.widgets || []).find((x) => x.name === 'image_file');"
                " if (!w) return null;"
                " const val = String(w.value || '');"
                " const nodes = Array.from(document.querySelectorAll('*')).filter((e) =>"
                "   e.children.length === 0 && e.textContent && e.textContent.trim() === val);"
                " const el = nodes[0];"
                " if (!el) return { value: val, found: false };"
                " const row = el.closest('[data-testid=\"node-widget\"]') || el.parentElement;"
                " return { value: val, found: true,"
                "   row: String(row.outerHTML).slice(0, 1200) }; })()"
            ), ensure_ascii=False))
            print("D) кнопки загрузки на странице:", json.dumps(await cdp.eval(
                "(() => { const out = [];"
                " for (const b of document.querySelectorAll('button, [role=\"button\"], input[type=\"file\"]')) {"
                "   const t = (b.getAttribute('aria-label') || b.getAttribute('title') || b.textContent || '').trim();"
                "   if (!t) continue;"
                "   if (/upload|загруз|файл|file/i.test(t)) out.push({ tag: b.tagName, t: t.slice(0, 40),"
                "     cls: String(b.className || '').slice(0, 60) });"
                " } return out.slice(0, 12); })()"
            ), ensure_ascii=False))
            print("C) dom-виджет в списке:", json.dumps(await cdp.eval(
                "(() => { const n = window.__diag;"
                " const w = (n.widgets || []).find((x) => x.name === 'preview');"
                " return { hasLayout: typeof w.computeLayoutSize, min: w.computeLayoutSize ? w.computeLayoutSize(n) : null }; })()"
            ), ensure_ascii=False))
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except Exception:
            chrome.kill()
        import shutil

        shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
