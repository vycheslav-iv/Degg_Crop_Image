"""Живая проба Degg_Crop_Image против запущенного ComfyUI.

Проверяет то, что не видно песочнице: нода реально регистрируется сервером,
принимает crop_rect из API, а фактические файлы на диске совпадают по размеру и
пикселям с локальным расчётом render_frames. Нужен ComfyUI на 127.0.0.1:8188
и его python (с torch/PIL):

    D:\\ComfyUI_windows_portable\\python_embeded\\python.exe tests/_probe_live_crop_image.py

Запускается отдельно — в check.py не входит (требует живой сервер).
"""
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

try:  # Windows-консоль иначе печатает русский текст в cp1251
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import torch  # noqa: E402
from PIL import Image  # noqa: E402

import crop_image as ci  # noqa: E402

HOST = "http://127.0.0.1:8188"
COMFY = Path(r"D:\ComfyUI_windows_portable\ComfyUI")
INPUT = COMFY / "input"
OUTPUT = COMFY / "output"
SOURCE = "example.png"

fails = []
oks = []


def check(label, cond, extra=""):
    (oks if cond else fails).append(label)
    tail = f"  [{extra}]" if extra and not cond else ""
    print(("  ok    " if cond else "  FAIL  ") + label + tail)


def api(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(HOST + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def run(graph, timeout=180):
    pid = api("/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex})["prompt_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        hist = api(f"/history/{pid}")
        entry = hist.get(pid)
        if entry and entry.get("status", {}).get("completed"):
            return entry
        if entry and entry.get("status", {}).get("status_str") == "error":
            raise RuntimeError(json.dumps(entry.get("status"), ensure_ascii=False)[:800])
        time.sleep(0.5)
    raise TimeoutError(f"прогон {pid} не завершился за {timeout}s")


def src_tensor():
    im = Image.open(INPUT / SOURCE).convert("RGB")
    arr = torch.frombuffer(bytearray(im.tobytes()), dtype=torch.uint8)
    return arr.reshape(1, im.height, im.width, 3).float() / 255.0


def crop_node(rect, mp, mult, fill, mask_ref=None):
    node = {
        "class_type": "DeggCropImage",
        "inputs": {
            "image": ["1", 0],
            "aspect_ratio": "free",
            "lock_ratio": False,
            "dim_outside": 50,
            "multiplicity": mult,
            "megapixels": mp,
            "resample": "lanczos",
            "fill": fill,
            "crop_rect": json.dumps(rect),
        },
    }
    if mask_ref:
        node["inputs"]["mask"] = mask_ref
    return node


def probe(name, graph, rect, mp, mult, fill, local_mask=None, save_node="20"):
    entry = run(graph)
    images = entry["outputs"][save_node]["images"]
    files = [OUTPUT / i.get("subfolder", "") / i["filename"] for i in images]
    for f in files:
        check(f"{name}: файл записан", f.exists(), str(f))

    expected, expected_mask, ew, eh = ci.render_frames(
        src_tensor(), local_mask, rect, fill, "lanczos", mp, mult
    )
    got = Image.open(files[0])
    check(f"{name}: размер {ew}x{eh}", got.size == (ew, eh), f"{got.size}")
    check(f"{name}: кадров {expected.shape[0]}", len(files) == int(expected.shape[0]), str(len(files)))

    arr = torch.frombuffer(bytearray(got.convert("RGBA").tobytes()), dtype=torch.uint8)
    arr = arr.reshape(got.height, got.width, 4).float() / 255.0
    ref = expected[0]
    if ref.shape[-1] == 4:
        diff = (arr - ref).abs()
        check(f"{name}: пиксели совпадают (max {float(diff.max()):.4f})", float(diff.max()) < 0.01)
    else:
        diff = (arr[..., :3] - ref).abs()
        check(f"{name}: пиксели совпадают (max {float(diff.max()):.4f})", float(diff.max()) < 0.01)

    # INT-выходы читаются через PreviewAny: width/height должны быть фактическими.
    for slot, want in ((3, ew), (4, eh)):
        out = entry["outputs"].get(f"9{slot - 3}", {})
        text = (out.get("text") or [""])[0] if out.get("text") else ""
        if text:
            check(f"{name}: выход {['width', 'height'][slot - 3]} = {want}", text.strip() == str(want), text)


def base_graph(crop, extra=None):
    g = {
        "1": {"class_type": "LoadImage", "inputs": {"image": SOURCE}},
        "2": crop,
        "20": {"class_type": "SaveImage",
               "inputs": {"images": ["2", 0], "filename_prefix": "degg_crop_probe"}},
        "90": {"class_type": "PreviewAny", "inputs": {"source": ["2", 2]}},
        "91": {"class_type": "PreviewAny", "inputs": {"source": ["2", 3]}},
    }
    if extra:
        g.update(extra)
    return g


def main():
    try:
        info = api("/object_info/DeggCropImage")
    except (urllib.error.URLError, ConnectionError) as exc:
        print(f"ComfyUI недоступен на {HOST}: {exc}")
        return 1
    node = info.get("DeggCropImage")
    check("нода зарегистрирована", bool(node))
    if not node:
        return summarise()
    check("4 выхода IMAGE/MASK/INT/INT", node["output"] == ["IMAGE", "MASK", "INT", "INT"], str(node["output"]))
    check("crop_rect скрыт из UI", node["input"]["required"]["crop_rect"][1].get("hidden") is True)

    # 1. Кадр внутри источника: без заливки, RGBA, точные пиксели.
    rect = {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}
    probe("кадр внутри", base_graph(crop_node(rect, 1.0, 8, "transparent")), rect, 1.0, 8, "transparent")

    # 2. Расширение за границы + чёрная заливка: RGB-источник остаётся 3ch.
    rect = {"x": -0.1, "y": -0.1, "w": 1.2, "h": 1.2}
    probe("расширение/чёрная", base_graph(crop_node(rect, 1.0, 8, "black")), rect, 1.0, 8, "black")

    # 3. Бюджет мегапикселей: большой кадр ужимается под 0.5 MP.
    rect = {"x": -5.0, "y": -5.0, "w": 11.0, "h": 11.0}
    probe("бюджет 0.5MP", base_graph(crop_node(rect, 0.5, 16, "gray")), rect, 0.5, 16, "gray")

    # 4. mask на входе заменяет альфу: ImageToMask(red) -> mask.
    rect = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    graph = base_graph(
        crop_node(rect, 1.0, 8, "transparent", mask_ref=["3", 0]),
        {"3": {"class_type": "ImageToMask",
               "inputs": {"image": ["1", 0], "channel": "red"}}},
    )
    im = Image.open(INPUT / SOURCE).convert("RGB")
    red = torch.frombuffer(bytearray(im.tobytes()), dtype=torch.uint8)
    red = (red.reshape(1, im.height, im.width, 3).float() / 255.0)[..., 0]
    probe("маска на входе", graph, rect, 1.0, 8, "transparent", local_mask=red)

    # 5. Батч: два кадра на входе -> два файла одного размера.
    rect = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}
    graph = base_graph(
        crop_node(rect, 1.0, 8, "transparent"),
        {"3": {"class_type": "ImageBatch",
               "inputs": {"image1": ["1", 0], "image2": ["1", 0]}}},
    )
    graph["2"]["inputs"]["image"] = ["3", 0]
    entry = run(graph)
    files = entry["outputs"]["20"]["images"]
    sizes = {Image.open(OUTPUT / f["filename"]).size for f in files}
    check("батч: 2 файла", len(files) == 2, str(len(files)))
    check("батч: один размер", len(sizes) == 1, str(sizes))
    return summarise()


def summarise():
    print("")
    print(f"ok: {len(oks)}   FAIL: {len(fails)}")
    if fails:
        print("ПРОБА НЕ ПРОЙДЕНА")
        return 1
    print("ПРОБА ПРОЙДЕНА")
    return 0


if __name__ == "__main__":
    sys.exit(main())
