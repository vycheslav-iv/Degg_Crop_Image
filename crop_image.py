"""Degg Crop Image — interactive crop with outpainting area.

The frame lives in normalized source coordinates (fractions of the source
image). Fractions may be negative or greater than 1: everything outside the
source is filled according to the `fill` widget, which is what makes the node
usable for outpainting.

Geometry (parsing, output size, intersection, destination mapping) is pure
Python so it can be tested without torch. Pixel work is isolated in
`_render`, which only uses `torch` and `comfy.utils.common_upscale`.
"""

from __future__ import annotations

import json
import math
import re

import torch
import torch.nn.functional as F

try:  # ComfyUI provides a proper lanczos; tests run without the comfy package.
    from comfy.utils import common_upscale as _common_upscale
except Exception:  # pragma: no cover - exercised by the sandbox tests
    _common_upscale = None


ASPECT_RATIOS = ["free", "1:1", "2:3", "3:2", "4:3", "3:4", "16:9", "9:16", "21:9"]
RATIO_VALUES = {
    "1:1": 1.0,
    "2:3": 2.0 / 3.0,
    "3:2": 3.0 / 2.0,
    "4:3": 4.0 / 3.0,
    "3:4": 3.0 / 4.0,
    "16:9": 16.0 / 9.0,
    "9:16": 9.0 / 16.0,
    "21:9": 21.0 / 9.0,
}
RESAMPLE_METHODS = ["lanczos", "bicubic", "bilinear", "nearest-exact"]
FILL_MODES = ["transparent", "black", "white", "gray"]

DEFAULT_RECT = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
MIN_FRACTION = 1e-6
MAX_FRACTION = 100.0
#: Smallest frame side kept by the ratio helper (parity with MIN_RATIO_PX in JS).
MIN_RATIO_PX = 8.0

#: Surfaces config below keeps the transport widget out of every renderer:
#: the frame is edited by the DOM editor, the JSON string is written by JS.
HIDDEN_OPTIONS = {
    "hidden": True,
    "advanced": True,
    "tooltip": "Internal: normalized crop rectangle, written by the node UI.",
}


def py_round(value: float) -> int:
    """``round()`` with banker's rounding (JavaScript default is half-up)."""
    floor = math.floor(value)
    diff = value - floor
    if abs(diff - 0.5) < 1e-9:
        return floor if floor % 2 == 0 else floor + 1
    return math.floor(value + 0.5)


def parse_rect(raw) -> dict:
    """Parse the transport value into ``{x, y, w, h}`` fractions.

    Accepts a JSON object, a JSON array ``[x, y, w, h]`` or an already built
    dict. Anything unusable falls back to the full image, never raises: a
    malformed widget must not break execution.
    """
    data = raw
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return dict(DEFAULT_RECT)
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return dict(DEFAULT_RECT)

    if isinstance(data, (list, tuple)):
        if len(data) != 4:
            return dict(DEFAULT_RECT)
        data = dict(zip(("x", "y", "w", "h"), data))
    if not isinstance(data, dict):
        return dict(DEFAULT_RECT)

    out = {}
    for key, fallback in DEFAULT_RECT.items():
        try:
            value = float(data.get(key, fallback))
        except (TypeError, ValueError):
            return dict(DEFAULT_RECT)
        if not math.isfinite(value):
            return dict(DEFAULT_RECT)
        out[key] = value

    out["w"] = min(max(out["w"], MIN_FRACTION), MAX_FRACTION)
    out["h"] = min(max(out["h"], MIN_FRACTION), MAX_FRACTION)
    out["x"] = min(max(out["x"], -MAX_FRACTION), MAX_FRACTION)
    out["y"] = min(max(out["y"], -MAX_FRACTION), MAX_FRACTION)
    if out["w"] <= MIN_FRACTION or out["h"] <= MIN_FRACTION:
        return dict(DEFAULT_RECT)
    return out


def snap(value: float, multiplicity: int) -> int:
    step = max(1, int(multiplicity))
    return max(step, py_round(value / step) * step)


def parse_output_size(raw: str) -> tuple[int, int] | None:
    """Parse a manual result size ``"W,H"`` (empty = derive from megapixels)."""
    text = str(raw or "").strip()
    if not text:
        return None
    parts = [p for p in re.split(r"[,x\s]+", text) if p]
    if len(parts) < 2:
        return None
    try:
        width = int(round(float(parts[0])))
        height = int(round(float(parts[1])))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _clamp_rect(rect: dict) -> dict:
    """Clamp a normalized rect (parity with ``clampFrac`` in the JS editor)."""
    out = {
        "x": float(rect["x"]),
        "y": float(rect["y"]),
        "w": float(rect["w"]),
        "h": float(rect["h"]),
    }
    out["x"] = min(max(out["x"], -MAX_FRACTION), MAX_FRACTION)
    out["y"] = min(max(out["y"], -MAX_FRACTION), MAX_FRACTION)
    if out["w"] <= MIN_FRACTION or out["h"] <= MIN_FRACTION:
        return dict(DEFAULT_RECT)
    return out


def frame_with_ratio(
    rect: dict, src_w: int, src_h: int, ratio: float, min_px: float = MIN_RATIO_PX
) -> dict:
    """Reshape the frame to ``ratio`` keeping its centre and area (JS parity)."""
    w0 = float(rect["w"]) * max(int(src_w), 1)
    h0 = float(rect["h"]) * max(int(src_h), 1)
    if not (src_w > 0 and src_h > 0 and w0 > 0 and h0 > 0 and ratio > 0):
        return dict(rect)
    area = max(w0 * h0, min_px * min_px)
    width = math.sqrt(area * ratio)
    height = width / ratio
    if width < min_px:
        width = min_px
        height = width / ratio
    if height < min_px:
        height = min_px
        width = height * ratio
    cx = float(rect["x"]) * src_w + w0 / 2.0
    cy = float(rect["y"]) * src_h + h0 / 2.0
    return _clamp_rect(
        {
            "x": (cx - width / 2.0) / src_w,
            "y": (cy - height / 2.0) / src_h,
            "w": width / src_w,
            "h": height / src_h,
        }
    )


def move_frame_to_offset(
    rect: dict, src_w: int, src_h: int, offset_x=None, offset_y=None
) -> dict:
    """Move the frame centre to image centre + offset, keeping its size.

    ``None`` on an axis keeps that axis' current position (the editor mirror
    must not be applied when only the other axis arrived by a wire).
    """
    width = float(rect["w"])
    height = float(rect["h"])
    src_w = max(int(src_w), 1)
    src_h = max(int(src_h), 1)
    if not (width > 0 and height > 0):
        return dict(rect)
    cur_cx = (float(rect["x"]) + width / 2.0) * src_w
    cur_cy = (float(rect["y"]) + height / 2.0) * src_h
    cx = cur_cx if offset_x is None else src_w / 2.0 + float(offset_x)
    cy = cur_cy if offset_y is None else src_h / 2.0 + float(offset_y)
    return _clamp_rect(
        {
            "x": (cx - width * src_w / 2.0) / src_w,
            "y": (cy - height * src_h / 2.0) / src_h,
            "w": width,
            "h": height,
        }
    )


def linked_inputs(prompt, unique_id) -> set:
    """Input names supplied by a WIRE in this prompt (a link is ``[id, slot]``).

    The editor mirrors ``frame_w``/``frame_h``/``center_x``/``center_y`` into
    widgets, so a literal there may be just a display copy. Only a real link
    proves the value was given to the node as an input.
    """
    if not isinstance(prompt, dict) or unique_id is None:
        return set()
    node = prompt.get(str(unique_id))
    if not isinstance(node, dict):
        return set()
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return set()
    return {
        name
        for name, value in inputs.items()
        if isinstance(value, list) and len(value) == 2
    }


def _wired_sockets(rest) -> set:
    """Input names whose value is a WIRE at validation/cache time.

    Two independent proofs, because validation runs before execution:

    * ``linked_inputs`` sees the literal ``[node_id, slot]`` in the prompt;
    * the engine resolves a link to ``None`` while validating (no outputs are
      cached yet), and such a name still arrives in the kwargs.

    A *string* is NOT a wire: old workflows carried the Mask Editor widget
    value in the socket named ``image``, and that file is really used.
    """
    wired = linked_inputs(rest.get("prompt"), rest.get("unique_id"))
    for socket in ("image", "mask"):
        if socket in rest and not isinstance(rest.get(socket), str):
            wired.add(socket)
    return wired


def output_size(
    rect_w: float,
    rect_h: float,
    megapixels: float,
    multiplicity: int,
    manual: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Target resolution for the frame, same maths as ComfyUI ResolutionSelector.

    1 MP is counted as 1024*1024 px and each side is rounded to the nearest
    multiple independently (``round``), exactly like ``nodes_resolution.py``,
    so the same ratio/MP/multiple gives the same numbers in both nodes.

    ``manual`` is the exact size the user (or the JS editor's reset-to-image)
    asked for: it wins over the megapixel target, but it goes through the SAME
    multiplicity step — otherwise "Кратность" would be silently ignored
    whenever the size came from the source image (file upload / «Сбросить»),
    which is exactly what was reported (2026-10-29). ``multiplicity=0`` means
    "no rounding", so the default keeps the source pixels 1:1.
    """
    step = max(1, int(multiplicity))
    if manual is not None:
        width = max(1, py_round(float(manual[0])))
        height = max(1, py_round(float(manual[1])))
        return (
            int(max(step, py_round(width / step) * step)),
            int(max(step, py_round(height / step) * step)),
        )

    rect_w = max(float(rect_w), MIN_FRACTION)
    rect_h = max(float(rect_h), MIN_FRACTION)
    mp = max(float(megapixels), 1e-4)

    ratio = rect_w / rect_h
    total_pixels = mp * 1024.0 * 1024.0
    base_w = math.sqrt(total_pixels * ratio)
    base_h = base_w / ratio

    width = max(step, py_round(base_w / step) * step)
    height = max(step, py_round(base_h / step) * step)
    return int(width), int(height)


def resolve_crop_rect(
    rect: dict,
    src_w: int,
    src_h: int,
    *,
    prompt=None,
    unique_id=None,
    center_x=0,
    center_y=0,
    frame_w=0,
    frame_h=0,
    output_size_raw="",
    megapixels=2.0,
    multiplicity=0,
) -> tuple[dict, tuple[int, int] | None]:
    """Turn the node INPUT values into the frame and the result size actually used.

    The frame itself lives in the stored ``crop_rect``; these inputs describe it
    (the editor writes them as mirrors, users and API scripts can drive them).
    Parity with the JS editor:

    * ``frame_w``/``frame_h`` set the result size and the frame follows it
      (area and centre kept) — like ``setResultSize``;
    * the ratio drop-down is a display setting: its value cannot be read here
      (a combo), so frame proportions change only through size or the editor;
    * centre offsets move the frame keeping its size, and only when they came
      by a wire: the editor also writes those widgets, and ``center_x=0`` from
      an API prompt must not silently recentre a hand-placed frame.

    Drop-down fields (``aspect_ratio``, ``fill``, ``resample``) are display
    settings and are deliberately NOT applied here.
    """
    rect = dict(rect)
    src_w = max(int(src_w), 1)
    src_h = max(int(src_h), 1)
    linked = linked_inputs(prompt, unique_id)

    manual = parse_output_size(output_size_raw)
    fw = max(int(frame_w or 0), 0)
    fh = max(int(frame_h or 0), 0)

    if fw or fh:
        rect_px_w = max(float(rect["w"]) * src_w, MIN_FRACTION)
        rect_px_h = max(float(rect["h"]) * src_h, MIN_FRACTION)
        if fw and fh:
            manual = (fw, fh)
        elif fw:
            manual = (fw, max(1, py_round(fw * rect_px_h / rect_px_w)))
        else:
            manual = (max(1, py_round(fh * rect_px_w / rect_px_h)), fh)
        out_w, out_h = output_size(
            rect_px_w, rect_px_h, megapixels, multiplicity, manual
        )
        manual = (out_w, out_h)
        rect = frame_with_ratio(rect, src_w, src_h, out_w / out_h)

    if linked & {"center_x", "center_y"}:
        rect = move_frame_to_offset(
            rect,
            src_w,
            src_h,
            int(center_x) if "center_x" in linked else None,
            int(center_y) if "center_y" in linked else None,
        )

    return rect, manual


def frame_geometry(
    rect: dict, src_w: int, src_h: int, out_w: int, out_h: int
) -> dict | None:
    """Map the normalized frame onto one source image.

    Returns source-space frame, the visible intersection and the destination
    rectangle inside the output canvas (rounded so adjacent edges never gap).
    ``None`` means the frame does not intersect the source at all.
    """
    src_w = max(int(src_w), 1)
    src_h = max(int(src_h), 1)

    fx = rect["x"] * src_w
    fy = rect["y"] * src_h
    fw = max(rect["w"] * src_w, MIN_FRACTION)
    fh = max(rect["h"] * src_h, MIN_FRACTION)

    ix0 = int(max(0, min(src_w, math.floor(fx))))
    iy0 = int(max(0, min(src_h, math.floor(fy))))
    ix1 = int(max(0, min(src_w, math.ceil(fx + fw))))
    iy1 = int(max(0, min(src_h, math.ceil(fy + fh))))

    if ix1 <= ix0 or iy1 <= iy0:
        return None

    sx = out_w / fw
    sy = out_h / fh

    dx0 = int(min(max(py_round((ix0 - fx) * sx), 0), out_w))
    dy0 = int(min(max(py_round((iy0 - fy) * sy), 0), out_h))
    dx1 = int(min(max(py_round((ix1 - fx) * sx), 0), out_w))
    dy1 = int(min(max(py_round((iy1 - fy) * sy), 0), out_h))

    if dx1 <= dx0 or dy1 <= dy0:
        return None

    return {
        "src_box": (ix0, iy0, ix1, iy1),
        "dst_box": (dx0, dy0, dx1, dy1),
    }


def _resize(tensor, width: int, height: int, method: str):
    """Resize an HWC tensor to ``width`` x ``height``."""
    if tensor.shape[1] == width and tensor.shape[0] == height:
        return tensor
    chw = tensor.permute(2, 0, 1)[None]
    if method == "lanczos" and _common_upscale is not None:
        out = _common_upscale(chw, width, height, "lanczos", "disabled")
    else:
        # F.interpolate режима "lanczos" НЕ знает (он живёт только в
        # comfy.utils.common_upscale): без него откатываемся на bicubic,
        # иначе ValueError на каждом кадре. torch запрещает antialias для
        # nearest-exact (и требует его для bilinear/bicubic).
        mode = "bicubic" if method == "lanczos" else method
        out = F.interpolate(
            chw,
            size=(height, width),
            mode=mode,
            antialias=mode in ("bilinear", "bicubic"),
        )
    return out[0].permute(1, 2, 0)


def _mask_alpha(
    mask, index: int, src_h: int, src_w: int, y0: int, y1: int, x0: int, x1: int
):
    """Видимая альфа по маске ComfyUI.

    В ComfyUI маска = 1 там, где область надо УБРАТЬ (LoadImage отдаёт
    ``1 - alpha``), поэтому видимая альфа = ``1 - маска``.

    Маска лежит в координатах ИСТОЧНИКА: её нужно ВЫРЕЗАТЬ по области кадра,
    а не сжимать в неё (иначе маска съезжает). Если размер маски не совпадает с
    источником (LoadImage для картинки без альфы отдаёт заглушку 64×64), она
    приводится к размеру источника.
    """
    frame = mask if getattr(mask, "dim", lambda: 2)() == 2 else mask[
        min(index, max(int(mask.shape[0]) - 1, 0))
    ]
    if tuple(frame.shape[:2]) != (src_h, src_w):
        frame = F.interpolate(
            frame[None, None], size=(src_h, src_w), mode="nearest-exact"
        )[0, 0]
    region = frame[y0:y1, x0:x1]
    return 1.0 - region


def input_image_files():
    """Image files of the input folder (like LoadImage).

    The list is only a hint for the frontend combo; the node itself reads the
    file by name at run time. Without ComfyUI (unit tests) the list is empty.
    """
    try:
        import os

        import folder_paths
    except Exception:
        return []
    try:
        base = folder_paths.get_input_directory()
        files = [f for f in os.listdir(base) if os.path.isfile(os.path.join(base, f))]
        files = folder_paths.filter_files_content_types(files, ["image"])
        return sorted(files)
    except Exception:
        return []


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _strip_annotation(name: str) -> str:
    """Strip the frontend annotation: ``"name.png [input]"`` -> ``"name.png"``."""
    text = str(name or "").strip()
    if text.startswith("["):  # старый формат: JSON-массив
        try:
            text = str(json.loads(text)[0]).strip()
        except Exception:
            return ""
    for marker in (" [output]", " [temp]", " [input]"):
        if text.lower().endswith(marker):
            text = text[: -len(marker)].strip()
            break
    return text


def input_file_path(name: str):
    """Absolute path of a file INSIDE the input folder, or ``None``.

    The name comes from a workflow, so it must not escape the input folder.
    Containment is checked on ``realpath`` with a common prefix, not with
    ``startswith``: a string prefix would accept ``../secret.png`` and a
    sibling ``input2`` folder. An absolute path is allowed only when it really
    lies inside the input folder (older workflows stored it that way).

    A bare name is also looked up in ``input/clipspace/``: frontends before the
    unified upload contract wrote the Mask Editor file there and put only its
    name in the widget, so without this fallback the painted layer is silently
    lost. A name WITH a subfolder is already the new layout and is not searched
    there, so the input root always wins.
    """
    text = _strip_annotation(name)
    if not text:
        return None
    try:
        import os

        import folder_paths

        base = os.path.realpath(folder_paths.get_input_directory())
    except Exception:
        return None
    text = text.replace("\\", "/")
    found = _input_path_checked(base, text)
    if found is not None:
        return found
    if os.path.isabs(text) or os.path.basename(text) != text:
        return None
    return _input_path_checked(base, os.path.join("clipspace", text))


def _input_path_checked(base: str, relative: str):
    """``relative`` resolved inside ``base`` (realpath containment) or ``None``."""
    try:
        import os

        candidate = relative if os.path.isabs(relative) else os.path.join(base, relative)
        path = os.path.realpath(candidate)
    except Exception:
        return None
    try:
        # Другой диск на Windows -> ValueError: у путей нет общего префикса.
        # Это вход из старого воркфлоу (абсолютный путь), а не сбой ноды.
        inside = os.path.commonpath([base, path]) == base
    except ValueError:
        return None
    if not inside or path == base:
        return None
    if not path.lower().endswith(IMAGE_EXTENSIONS):
        return None
    try:
        return path if os.path.isfile(path) else None
    except OSError:
        return None


def load_source_file(name: str):
    """Load an image file from the input folder (like LoadImage).

    Returns ``(image_tensor, mask)`` or ``(None, None)`` if the file is
    missing/outside the input folder/unreadable. The mask follows the ComfyUI
    convention: 1 = the area is hidden (the file's transparent alpha).
    EXIF orientation is applied, as LoadImage does, so the pixels match the
    preview the browser shows.
    """
    if not name:
        return None, None
    path = input_file_path(name)
    if path is None:
        return None, None
    try:
        import numpy as np
        from PIL import Image, ImageOps
    except Exception:
        return None, None

    try:
        with Image.open(path) as handle:
            upright = ImageOps.exif_transpose(handle) or handle
            image = upright.convert("RGB")
            array = np.array(image).astype(np.float32) / 255.0
            alpha = None
            if "A" in upright.getbands():
                alpha = 1.0 - np.array(upright.getchannel("A")).astype(np.float32) / 255.0
    except Exception:
        return None, None

    tensor = torch.from_numpy(array)[None, ...]
    mask = None
    if alpha is not None:
        mask = torch.from_numpy(alpha)[None, ...]
    return tensor, mask


def painted_matches_source(painted, source, tolerance: float = 0.08) -> bool:
    """True when the editor's file is the SAME picture as the source.

    The standard Mask Editor saves one file: RGB = the image it was opened on
    plus the Paint layer, alpha = the inverted mask. When the image arrives by
    wire, that file is still the only place the Paint layer lives — but it may
    be left over from a previous picture. Taking it blindly would put a foreign
    image on the output, so the base is compared first.

    The comparison uses the MEDIAN difference over a sampling grid: paint
    strokes are a minority of the pixels, so for the right picture the median
    is zero, while a leftover file of another picture differs nearly everywhere.
    An 8-bit preview PNG against a float tensor differs by ~1/255, far below
    the tolerance.
    """
    if painted is None or source is None:
        return False
    try:
        p_shape = tuple(int(v) for v in painted.shape)
        s_shape = tuple(int(v) for v in source.shape)
    except Exception:
        return False
    if len(p_shape) != 4 or len(s_shape) != 4:
        return False
    if p_shape[0] != 1 or p_shape[1] != s_shape[1] or p_shape[2] != s_shape[2]:
        return False
    height, width = p_shape[1], p_shape[2]
    channels = min(p_shape[3], s_shape[3], 3)
    if height <= 0 or width <= 0 or channels <= 0:
        return False
    step_y = max(1, height // 48)
    step_x = max(1, width // 48)
    diffs = []
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            worst = 0.0
            for c in range(channels):
                try:
                    delta = abs(float(painted[0, y, x, c]) - float(source[0, y, x, c]))
                except Exception:
                    return False
                if delta > worst:
                    worst = delta
            diffs.append(worst)
    if not diffs:
        return False
    diffs.sort()
    return diffs[len(diffs) // 2] <= tolerance


def _fill_color(mode: str, channels: int):
    if mode == "black":
        rgb = (0.0, 0.0, 0.0)
    elif mode == "white":
        rgb = (1.0, 1.0, 1.0)
    else:
        rgb = (0.5, 0.5, 0.5)
    if channels == 4:
        alpha = 0.0 if mode == "transparent" else 1.0
        return rgb[0], rgb[1], rgb[2], alpha
    return rgb


def render_frames(
    image,
    mask,
    rect: dict,
    fill: str,
    resample: str,
    megapixels: float = 2.0,
    multiplicity: int = 0,
    manual_size: tuple[int, int] | None = None,
):
    """Render the frame for every image in the batch.

    The output resolution is derived from the first frame, so a batch with
    mixed source sizes still produces one rectangular tensor. Every frame is
    mapped through the same normalized frame.
    """
    batch = int(image.shape[0])
    _, src_h, src_w, src_c = (int(v) for v in image.shape[:4])
    out_w, out_h = output_size(
        rect["w"] * src_w, rect["h"] * src_h, megapixels, multiplicity, manual_size
    )
    has_alpha = src_c >= 4 or mask is not None or fill == "transparent"
    channels = 4 if has_alpha else 3

    color = _fill_color(fill, channels)
    # Канва живёт на устройстве источника: в ComfyUI картинка после сэмплера
    # уже на cuda, а zeros() по умолчанию cpu — присваивание кадра тогда
    # делало бы тихую копию через PCIe на каждый кадр.
    device = getattr(image, "device", None)
    canvas = torch.zeros(
        (batch, out_h, out_w, channels), dtype=torch.float32, device=device
    )
    # Канва заливается ЦВЕТОМ всегда, в том числе для 'transparent': у прозрачной
    # заливки RGB = серый (0.5), альфа = 0. Раньше там были нули, и превью без
    # альфы показывало аутпейнт ЧЁРНЫМ.
    canvas[...] = torch.tensor(color, device=device)

    mask_out = torch.zeros(
        (batch, out_h, out_w), dtype=torch.float32, device=device
    )
    if fill != "transparent":
        mask_out[...] = 1.0

    for index in range(batch):
        frame = image[index]
        geo = frame_geometry(rect, src_w, src_h, out_w, out_h)
        if geo is None:
            continue

        (ix0, iy0, ix1, iy1) = geo["src_box"]
        (dx0, dy0, dx1, dy1) = geo["dst_box"]

        patch = frame[iy0:iy1, ix0:ix1, :]
        if mask is not None:
            # Маска ComfyUI: 1 = убрать. Видимая альфа = 1 - маска. Маска идёт
            # с LoadImage с cpu (и float32), картинка может быть cuda/fp16:
            # torch.cat ничего не приводит, поэтому приводим явно.
            alpha = _mask_alpha(
                mask, index, src_h, src_w, iy0, iy1, ix0, ix1
            )
            patch = torch.cat(
                [
                    patch[..., :3],
                    alpha[..., None].to(dtype=patch.dtype, device=patch.device),
                ],
                dim=-1,
            )
        elif channels == 4:
            if int(patch.shape[-1]) >= 4:
                patch = patch[..., :4]
            else:
                patch = torch.cat(
                    [
                        patch[..., :3],
                        torch.ones(
                            (iy1 - iy0, ix1 - ix0, 1),
                            dtype=patch.dtype,
                            device=patch.device,
                        ),
                    ],
                    dim=-1,
                )

        patch = _resize(patch, dx1 - dx0, dy1 - dy0, resample)
        canvas[index, dy0:dy1, dx0:dx1, :] = patch[..., :channels]
        mask_out[index, dy0:dy1, dx0:dx1] = (
            patch[..., 3] if int(patch.shape[-1]) >= 4 else 1.0
        )

    return canvas, mask_out, out_w, out_h


class DeggCropImage:
    """Crop / expand an image using an interactive frame on the node body."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "megapixels": (
                    "FLOAT",
                    {
                        "default": 2.0,
                        "min": 0.1,
                        "max": 64.0,
                        "step": 0.1,
                        "tooltip": "Target output area in megapixels (1 MP = 1024 x 1024 px).",
                    },
                ),
                "multiplicity": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 128,
                        "step": 4,
                        "tooltip": "Round the result size to a multiple of this value "
                        "(0 = no rounding). Applies to the crop, to the source "
                        "image and to the manual size.",
                    },
                ),
                "resample": (
                    RESAMPLE_METHODS,
                    {
                        "default": "lanczos",
                        "tooltip": "Resampling filter used when the frame is rescaled.",
                    },
                ),
                "fill": (
                    FILL_MODES,
                    {
                        "default": "transparent",
                        "tooltip": "Color of the area outside the source image.",
                    },
                ),
                "dim_outside": (
                    "INT",
                    {
                        "default": 50,
                        "min": 0,
                        "max": 100,
                        "tooltip": "Display only: darken the area outside the frame.",
                    },
                ),
            },
            # Скрытые служебные входы — необязательные: API не должен требовать
            # их передавать (значения по умолчанию корректны).
            "optional": {
                # Источник: подключённый сокет ИЛИ файл из input. Если сокет
                # подключён — он главный; если нет — берётся файл (и его альфа
                # становится маской, как в LoadImage).
                "image": (
                    "IMAGE",
                    {
                        "tooltip": "Source image batch. Leave unconnected to use the "
                        "file selected below.",
                    },
                ),
                # Файл из папки input — запасной источник, когда сокет не
                # подключён. Комбо с image_upload: штатная кнопка загрузки
                # файла появляется прямо в этом виджете (WidgetSelect).
                # Вход необязательный: сервер проверяет значение комбо по
                # списку, и обязательный вход заставил бы API подставлять
                # имя файла даже при подключённом сокете.
                "image_file": (
                    input_image_files(),
                    {
                        "image_upload": True,
                        "tooltip": "Image file from the input folder. Used when the "
                        "image socket is not connected.",
                    },
                ),
                # Файл с маской, нарисованной в штатном Mask Editor ComfyUI.
                # Фронтенд сохраняет результат рисования в папку input и
                # кладёт ссылку в служебный виджет; интерфейс переносит её
                # сюда. Альфа файла = инвертированная маска (конвенция
                # ComfyUI: 1 = убрать), поэтому маска = 1 - альфа.
                "mask_file": (
                    "STRING",
                    {
                        "default": "",
                        "hidden": True,
                        "advanced": True,
                        "tooltip": "Internal: image file with the mask painted in "
                        "the standard Mask Editor. Written by the node UI.",
                    },
                ),
                # Скрытые служебные значения (состояние кадра и ручной размер
                # результата) и элементы управления редактором. Сервер читает
                # только crop_rect/output_size/megapixels/multiplicity —
                # остальное нужно интерфейсу.
                "crop_rect": ("STRING", HIDDEN_OPTIONS),
                "output_size": (
                    "STRING",
                    {
                        "hidden": True,
                        "advanced": True,
                        "tooltip": "Internal: manual result size 'W,H' in pixels, "
                        "written by the node UI. Empty = size is derived from megapixels.",
                    },
                ),
                # Интерфейсные элементы управления редактором кадра. Сервер их не
                # читает (состояние — в crop_rect/output_size), но объявлять их
                # необязательными нужно, чтобы запуск через API не требовал
                # передавать фиктивные значения. Порядок виджетов на теле ноды
                # задаёт редактор (WIDGET_ORDER в web/js/crop_image.js).
                "lock_ratio": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "Locked",
                        "label_off": "Free",
                        "tooltip": "Keep the selected aspect ratio while dragging.",
                    },
                ),
                "center_x": (
                    "INT",
                    {
                        "default": 0,
                        "min": -100000,
                        "max": 100000,
                        "step": 1,
                        "tooltip": "Horizontal offset of the frame centre from the image centre, in pixels.",
                    },
                ),
                "center_y": (
                    "INT",
                    {
                        "default": 0,
                        "min": -100000,
                        "max": 100000,
                        "step": 1,
                        "tooltip": "Vertical offset of the frame centre from the image centre, in pixels.",
                    },
                ),
                "frame_w": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 100000,
                        "step": 1,
                        "tooltip": "Final result width in pixels. The frame proportions "
                        "follow it; megapixels show the equivalent value.",
                    },
                ),
                "frame_h": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 100000,
                        "step": 1,
                        "tooltip": "Final result height in pixels. The frame proportions "
                        "follow it; megapixels show the equivalent value.",
                    },
                ),
                "aspect_ratio": (
                    ASPECT_RATIOS,
                    {
                        "default": "free",
                        "tooltip": "Frame aspect ratio. Inactive while Lock Ratio is off.",
                    },
                ),
                "mask": (
                    "MASK",
                    {
                        "tooltip": "Optional mask for the source (ComfyUI convention: "
                        "1 = remove). Visible alpha = 1 - mask; replaces the source alpha.",
                    },
                ),
                "golden_ratio": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Display only: golden-ratio grid over the frame.",
                    },
                ),
                "snap_edges": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Display only: while dragging, frame edges snap to the image "
                        "bounds. Alt while dragging disables snapping too.",
                    },
                ),
            },
            # Prompt and node id are needed to tell a value supplied by a WIRE
            # from the editor's display copy of the same field (see
            # resolve_crop_rect).
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "INT")
    RETURN_NAMES = ("image", "mask", "width", "height")
    OUTPUT_TOOLTIPS = (
        "Cropped image batch.",
        "Alpha of the result (filled area = 0 when transparent).",
        "Actual output width.",
        "Actual output height.",
    )
    FUNCTION = "crop"
    CATEGORY = "My_custom_nodes/Image"
    DESCRIPTION = (
        "Crop any part of the image or extend the frame outside its borders. "
        "The frame is edited directly on the node and stored in the workflow."
    )

    @classmethod
    def VALIDATE_INPUTS(cls, image_file="", mask_file="", **rest):
        """A missing file (or a path outside the input folder) fails before run.

        LoadImage behaves the same way. An empty value is legal: the source may
        arrive over a wire while the file widget stays empty.

        A wired socket replaces its file widget completely, so a stale file
        left in the widget must not fail validation: the node never opens it
        (a workflow stays usable after the file is renamed or removed).
        """
        wired = _wired_sockets(rest)
        for name, socket in ((image_file, "image"), (mask_file, "mask")):
            if socket in wired:
                continue
            text = _strip_annotation(name) if name else ""
            if text and input_file_path(text) is None:
                return f"Invalid file (missing or outside the input folder): {text}"
        return True

    @classmethod
    def IS_CHANGED(cls, image_file="", mask_file="", **rest):
        """Stamp of the source files: a re-upload under the SAME name reaches the cache.

        Without files (source over a wire) the stamp is constant: the upstream
        hash is responsible for the picture, so there is no reason to break the
        cache. A file behind a wired socket is not read at all, so its re-upload
        must not invalidate the node either.
        """
        wired = _wired_sockets(rest)
        parts = []
        for name, socket in ((image_file, "image"), (mask_file, "mask")):
            if socket in wired:
                continue
            path = input_file_path(name) if name else None
            if path is None:
                continue
            try:
                import os

                stat = os.stat(path)
            except OSError:
                continue
            parts.append(f"{os.path.basename(path)}:{stat.st_size}:{stat.st_mtime_ns}")
        return "|".join(parts)

    def crop(
        self,
        image=None,
        image_file="",
        mask_file="",
        aspect_ratio="free",
        lock_ratio=True,
        center_x=0,
        center_y=0,
        frame_w=0,
        frame_h=0,
        dim_outside=50,
        multiplicity=0,
        megapixels=2.0,
        resample="lanczos",
        fill="transparent",
        crop_rect="",
        output_size="",
        golden_ratio=False,  # только отображение (сетка в превью), сервер не читает
        snap_edges=True,  # только отображение (магнит краёв), сервер не читает
        mask=None,
        prompt=None,
        unique_id=None,
    ):
        # Источник: подключённый сокет главный; иначе — файл из input
        # (и его альфа становится маской, как в LoadImage).
        # Вход приходит не только по проводу: фронтенд кладёт в промпт значения
        # виджетов ПО ИМЕНИ, поэтому в `image` может оказаться строка (старые
        # воркфлоу: служебный виджет Mask Editor назывался `image`). Строка —
        # это не картинка, значит источник не подан: читаем файл, а не отдаём
        # пустой результат.
        # Файл штатного Mask Editor — это результат рисования целиком:
        # RGB = исходная картинка + слой Paint (цветная кисть), альфа =
        # инвертированная маска. Как LoadImage, берём из него И картинку, И
        # маску: если взять только маску, покраска цветной кистью теряется.
        painted_image = None
        painted_mask = None
        if mask_file:
            painted_image, painted_mask = load_source_file(mask_file)

        file_mask = None
        if isinstance(image, (list, tuple)):
            # Батч может прийти списком кадров: это поданная картинка, а не
            # повод молча подставить файл из виджета (иначе на выходе чужое).
            image = image[0] if image else None
        if image is not None and not hasattr(image, "shape"):
            image = None
        if image is None:
            if painted_image is not None:
                image = painted_image
                file_mask = painted_mask
            else:
                image, file_mask = load_source_file(image_file)
        elif painted_image is not None and painted_matches_source(painted_image, image):
            # Картинка подана сокетом, но файл редактора — ЭТА ЖЕ картинка со
            # слоем Paint: берём его, иначе покраска по картинке из кабеля
            # теряется (жалоба 2026-10-23: нарисовал, сохранил, а на выходе
            # исходник). Основу сверяем, чтобы не подставить картинку от
            # прошлого источника. В батче красим первый кадр — редактор
            # открывался по нему.
            if int(image.shape[0]) == 1:
                image = painted_image.to(device=image.device, dtype=image.dtype)
            else:
                # Файл редактора читается с cpu/float32, батч может быть на
                # cuda: torch.cat устройства и dtype не приводит.
                image = torch.cat(
                    [
                        painted_image.to(device=image.device, dtype=image.dtype),
                        image[1:],
                    ],
                    dim=0,
                )
        # Маска: явный вход важнее файла с нарисованной маской, тот важнее
        # альфы исходного файла.
        if mask is None:
            if painted_mask is not None:
                mask = painted_mask
            elif file_mask is not None:
                mask = file_mask

        if image is None or int(getattr(image, "shape", (0,))[0] or 0) == 0:
            # Пустой результат: размер задан кратностью, но torch не примет
            # отрицательный/нулевой размер — это не ошибка пользователя.
            empty = torch.zeros((1, max(int(multiplicity), 1), max(int(multiplicity), 1), 3))
            return (empty, torch.zeros((1, max(int(multiplicity), 1), max(int(multiplicity), 1))), 0, 0)

        rect = parse_rect(crop_rect)
        method = resample if resample in RESAMPLE_METHODS else "lanczos"
        fill_mode = fill if fill in FILL_MODES else "transparent"

        # Значения входов describe the frame; the frame itself is crop_rect.
        # Wired centre offsets, frame size and the ratio preset are applied
        # here, once the source size is known (see resolve_crop_rect).
        rect, manual = resolve_crop_rect(
            rect,
            int(image.shape[2]),
            int(image.shape[1]),
            prompt=prompt,
            unique_id=unique_id,
            center_x=center_x,
            center_y=center_y,
            frame_w=frame_w,
            frame_h=frame_h,
            output_size_raw=output_size,
            megapixels=megapixels,
            multiplicity=multiplicity,
        )

        out, out_mask, width, height = render_frames(
            image,
            mask,
            rect,
            fill_mode,
            method,
            float(megapixels),
            int(multiplicity),
            manual,
        )
        return (out, out_mask, int(width), int(height))


NODE_CLASS_MAPPINGS = {"DeggCropImage": DeggCropImage}
NODE_DISPLAY_NAME_MAPPINGS = {"DeggCropImage": "Degg Crop Image"}
