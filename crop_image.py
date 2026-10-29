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
        mode = "nearest-exact" if method == "nearest-exact" else method
        out = F.interpolate(chw, size=(height, width), mode=mode, antialias=True)
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
    candidate = text if os.path.isabs(text) else os.path.join(base, text)
    try:
        path = os.path.realpath(candidate)
    except Exception:
        return None
    if os.path.commonpath([base, path]) != base or path == base:
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
    canvas = torch.zeros((batch, out_h, out_w, channels), dtype=torch.float32)
    # Канва заливается ЦВЕТОМ всегда, в том числе для 'transparent': у прозрачной
    # заливки RGB = серый (0.5), альфа = 0. Раньше там были нули, и превью без
    # альфы показывало аутпейнт ЧЁРНЫМ.
    canvas[...] = torch.tensor(color)

    mask_out = torch.zeros((batch, out_h, out_w), dtype=torch.float32)
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
            # Маска ComfyUI: 1 = убрать. Видимая альфа = 1 - маска.
            alpha = _mask_alpha(
                mask, index, src_h, src_w, iy0, iy1, ix0, ix1
            )
            patch = torch.cat([patch[..., :3], alpha[..., None]], dim=-1)
        elif channels == 4:
            if int(patch.shape[-1]) >= 4:
                patch = patch[..., :4]
            else:
                patch = torch.cat(
                    [patch[..., :3], torch.ones((iy1 - iy0, ix1 - ix0, 1))], dim=-1
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
                        "tooltip": "Target output area in megapixels (1 MP = 1,000,000 px).",
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
                        "tooltip": "Width of the crop region in source pixels. "
                        "0 keeps the current frame size.",
                    },
                ),
                "frame_h": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 100000,
                        "step": 1,
                        "tooltip": "Height of the crop region in source pixels. "
                        "0 keeps the current frame size.",
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
                        "tooltip": "Optional alpha for the source (1 = opaque). "
                        "Replaces the source alpha.",
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
        """
        for name in (image_file, mask_file):
            text = _strip_annotation(name) if name else ""
            if text and input_file_path(text) is None:
                return f"Invalid file (missing or outside the input folder): {text}"
        return True

    @classmethod
    def IS_CHANGED(cls, image_file="", mask_file="", **rest):
        """Stamp of the source files: a re-upload under the SAME name reaches the cache.

        Without files (source over a wire) the stamp is constant: the upstream
        hash is responsible for the picture, so there is no reason to break the
        cache.
        """
        parts = []
        for name in (image_file, mask_file):
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
                image = painted_image
            else:
                image = torch.cat([painted_image, image[1:]], dim=0)
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

        out, out_mask, width, height = render_frames(
            image,
            mask,
            rect,
            fill_mode,
            method,
            float(megapixels),
            int(multiplicity),
            parse_output_size(output_size),
        )
        return (out, out_mask, int(width), int(height))


NODE_CLASS_MAPPINGS = {"DeggCropImage": DeggCropImage}
NODE_DISPLAY_NAME_MAPPINGS = {"DeggCropImage": "Degg Crop Image"}
