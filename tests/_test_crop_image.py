"""Functional test for Degg_Crop_Image (Python) — no ComfyUI, no torch.

The node is pure geometry plus a thin pixel layer. `torch` and
`torch.nn.functional` are replaced here by a small N-dimensional list tensor
with real torch semantics for the operations the node uses: slicing with
ints/slices/Ellipsis/None, assignment through a selection, `permute`, `cat`
and `interpolate`. Values therefore survive the round trip and the test can
check WHICH pixels ended up where, not merely the output shape.

Run:  cd Degg_Crop_Image && python tests/_test_crop_image.py
"""

import importlib.util
import itertools
import math
import sys
import types
from pathlib import Path

NODE = Path(__file__).resolve().parent.parent / "crop_image.py"

fails = []
oks = []


def _p(text):
    """Console-safe printing (Windows cp1251 cannot encode every glyph)."""
    try:
        enc = sys.stdout.encoding or "utf-8"
        sys.stdout.write(str(text).encode(enc, "replace").decode(enc, "replace") + "\n")
    except Exception:
        pass


def check(name, cond, extra=""):
    (oks if cond else fails).append(name)
    _p(("  ok  " if cond else "  FAIL") + f"  {name}" + (f"  [{extra}]" if extra and not cond else ""))


# ─────────────────────────── minimal torch stub ────────────────────────────


def _prod(values):
    total = 1
    for value in values:
        total *= int(value)
    return total


class T:
    """Flat, row-major tensor with torch-compatible basic indexing."""

    def __init__(self, shape, data=None, fill=0.0):
        self.shape = tuple(int(s) for s in shape)
        if any(s < 0 for s in self.shape):
            raise ValueError(f"negative shape {self.shape}")
        size = _prod(self.shape)
        if data is None:
            self.data = [fill] * size
        else:
            self.data = list(data)
            if len(self.data) != size:
                raise ValueError(f"data {len(self.data)} != shape {self.shape}")

    def dim(self):
        return len(self.shape)

    def _strides(self):
        strides = [1] * len(self.shape)
        for i in range(len(self.shape) - 2, -1, -1):
            strides[i] = strides[i + 1] * self.shape[i + 1]
        return strides

    def _norm_key(self, key):
        if not isinstance(key, tuple):
            key = (key,)
        if sum(1 for k in key if k is Ellipsis) > 1:
            raise IndexError("multiple Ellipsis")
        named = [k for k in key if k is not None and k is not Ellipsis]
        missing = len(self.shape) - len(named)
        if missing < 0:
            raise IndexError(f"too many indices in {key} for shape {self.shape}")
        # torch allows fewer indices than dimensions (implicit full slices),
        # and None inserts a size-1 axis without consuming a dimension.
        if Ellipsis in key:
            out = []
            for k in key:
                if k is Ellipsis:
                    out.extend([slice(None)] * missing)
                else:
                    out.append(k)
            return tuple(out)
        return tuple(key) + (slice(None),) * missing

    def _selection(self, key):
        # kind: "int" drops the dimension, "slice" keeps it, "new" adds size 1
        ranges = []
        dim = 0
        for k in self._norm_key(key):
            if k is None:
                ranges.append(([0], "new"))
                continue
            size = self.shape[dim]
            dim += 1
            if isinstance(k, int):
                index = k + size if k < 0 else k
                if not 0 <= index < size:
                    raise IndexError(f"index {k} out of range {size}")
                ranges.append(([index], "int"))
            elif isinstance(k, slice):
                ranges.append((list(range(*k.indices(size))), "slice"))
            else:
                raise TypeError(f"unsupported key {k!r}")
        iter_dims = [idxs for idxs, kind in ranges if kind != "new"]
        out_shape = [1 if kind == "new" else len(idxs) for idxs, kind in ranges if kind != "int"]
        strides = self._strides()
        flat = []
        for combo in itertools.product(*iter_dims):
            flat.append(sum(i * s for i, s in zip(combo, strides)))
        return flat, out_shape

    def __getitem__(self, key):
        flat, shape = self._selection(key)
        if not shape:
            return self.data[flat[0]]
        return T(shape, [self.data[i] for i in flat])

    def __setitem__(self, key, value):
        flat, shape = self._selection(key)
        if isinstance(value, T):
            data = _broadcast(value, tuple(shape))
            for i, v in zip(flat, data):
                self.data[i] = v
        elif isinstance(value, (int, float)):
            for i in flat:
                self.data[i] = float(value)
        else:
            raise TypeError(f"unsupported value {type(value)}")

    def permute(self, *dims):
        ndim = len(self.shape)
        dims = tuple(d % ndim for d in dims) if dims else tuple(range(ndim - 1, -1, -1))
        if sorted(dims) != list(range(ndim)):
            raise ValueError(f"bad permute {dims}")
        new_shape = [self.shape[d] for d in dims]
        strides = self._strides()
        data = []
        for coord in itertools.product(*[range(s) for s in new_shape]):
            src = [0] * ndim
            for out_axis, src_axis in enumerate(dims):
                src[src_axis] = coord[out_axis]
            data.append(self.data[sum(c * s for c, s in zip(src, strides))])
        return T(new_shape, data)

    def __sub__(self, other):
        if isinstance(other, (int, float)):
            return T(self.shape, [v - float(other) for v in self.data])
        if isinstance(other, T):
            other_data = _broadcast(other, self.shape)
            return T(self.shape, [a - b for a, b in zip(self.data, other_data)])
        raise TypeError(f"unsupported operand {type(other)}")

    def __rsub__(self, other):
        if isinstance(other, (int, float)):
            return T(self.shape, [float(other) - v for v in self.data])
        raise TypeError(f"unsupported operand {type(other)}")

    def __repr__(self):
        return f"T{self.shape}"



def _broadcast(value, shape):
    """Broadcast like torch: same shape, a scalar, or a matching suffix."""
    if tuple(value.shape) == tuple(shape):
        return list(value.data)
    if len(value.data) == 1:
        return value.data * _prod(shape)
    if len(value.shape) <= len(shape) and tuple(shape[-len(value.shape):]) == tuple(value.shape):
        reps = _prod(shape[:-len(value.shape)]) if len(value.shape) < len(shape) else 1
        return list(value.data) * reps
    raise ValueError(f"cannot broadcast {value.shape} to {shape}")

def _zeros(shape, dtype=None):
    return T(shape, fill=0.0)


def _ones(shape, dtype=None):
    return T(shape, fill=1.0)


def _tensor(values):
    values = list(values)
    return T((len(values),), [float(v) for v in values])


def _cat(tensors, dim=0):
    tensors = list(tensors)
    axis = dim % len(tensors[0].shape)
    base = list(tensors[0].shape)
    if any(len(t.shape) != len(base) for t in tensors):
        raise ValueError("rank mismatch")
    total = 0
    for t in tensors:
        for i, s in enumerate(t.shape):
            if i != axis and s != base[i]:
                raise ValueError("shape mismatch")
        total += t.shape[axis]
    out_shape = list(base)
    out_shape[axis] = total
    strides = T(out_shape)._strides()
    data = [0.0] * _prod(out_shape)
    offset = 0
    for t in tensors:
        for coord in itertools.product(*[range(s) for s in t.shape]):
            out_coord = list(coord)
            out_coord[axis] += offset
            data[sum(c * s for c, s in zip(out_coord, strides))] = t[tuple(coord)]
        offset += t.shape[axis]
    return T(out_shape, data)


def _interpolate(x, size, mode="nearest", antialias=False):
    """Nearest-neighbour resample of the last two dims (shape is what matters)."""
    out_h, out_w = (int(v) for v in size)
    lead = x.shape[:-2]
    in_h, in_w = x.shape[-2:]
    if in_h == out_h and in_w == out_w:
        return T(x.shape, x.data)
    ys = [min(in_h - 1, int(i * in_h / out_h)) for i in range(out_h)]
    xs = [min(in_w - 1, int(i * in_w / out_w)) for i in range(out_w)]
    data = []
    for lead_coord in itertools.product(*[range(s) for s in lead]):
        for y in ys:
            for px in xs:
                data.append(x[tuple(lead_coord) + (y, px)])
    return T(tuple(lead) + (out_h, out_w), data)


torch_mod = types.ModuleType("torch")
nn_mod = types.ModuleType("torch.nn")
fn_mod = types.ModuleType("torch.nn.functional")
torch_mod.nn = nn_mod
nn_mod.functional = fn_mod
torch_mod.float32 = "float32"
torch_mod.zeros = _zeros
torch_mod.ones = _ones
torch_mod.tensor = _tensor
torch_mod.cat = _cat
fn_mod.interpolate = _interpolate
sys.modules["torch"] = torch_mod
sys.modules["torch.nn"] = nn_mod
sys.modules["torch.nn.functional"] = fn_mod

spec = importlib.util.spec_from_file_location("crop_image_under_test", NODE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


# ─────────────────────────────── helpers ───────────────────────────────────


def make_image(batch, height, width, channels=3):
    """Distinct pixel values: index-based, so a wrong pixel is detectable."""
    data = []
    for b in range(batch):
        for y in range(height):
            for x in range(width):
                for c in range(channels):
                    data.append(b * 10000.0 + y * 100.0 + x * 10.0 + c)
    return T((batch, height, width, channels), data)


def close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


# ─────────────────────────────── 1. py_round ───────────────────────────────

check("py_round(0.5) == 0 (banker's)", mod.py_round(0.5) == 0, mod.py_round(0.5))
check("py_round(1.5) == 2", mod.py_round(1.5) == 2)
check("py_round(2.5) == 2", mod.py_round(2.5) == 2)
check("py_round(-0.5) == 0", mod.py_round(-0.5) == 0)
check("py_round(2.4) == 2", mod.py_round(2.4) == 2)

# ────────────────────────────── 2. parse_rect ──────────────────────────────

full = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
check("parse_rect('') -> full", mod.parse_rect("") == full)
check("parse_rect('not json') -> full", mod.parse_rect("{{") == full)
check("parse_rect([0.1,0.2,0.3,0.4])", mod.parse_rect("[0.1,0.2,0.3,0.4]") == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4})
check("parse_rect(w=0) -> full", mod.parse_rect({"x": 0, "y": 0, "w": 0, "h": 1}) == full)
check("parse_rect(NaN) -> full", mod.parse_rect({"x": float("nan"), "y": 0, "w": 1, "h": 1}) == full)
huge = mod.parse_rect({"x": -1e9, "y": 1e9, "w": 1e9, "h": 1e9})
check("parse_rect clamps transport values", huge["w"] == 100.0 and huge["x"] == -100.0 and huge["y"] == 100.0, huge)

# ─────────────────────────────── 3. snap ───────────────────────────────────

check("snap(1000, 8) == 1000", mod.snap(1000, 8) == 1000)
check("snap(1001, 8) == 1000", mod.snap(1001, 8) == 1000)
check("snap(1005, 8) == 1008", mod.snap(1005, 8) == 1008)
check("snap(0, 8) == 8", mod.snap(0, 8) == 8)
check("snap(3, 1024) == 1024", mod.snap(3, 1024) == 1024)

# ─────────────────────────── 4. output_size ────────────────────────────────

# Паритет с ResolutionSelector (nodes_resolution.py): 1 MP = 1024², каждая
# сторона округляется независимо до ближайшей кратности.
square = mod.output_size(1000.0, 1000.0, 2.0, 8)
check("output_size 2MP/8 -> 1448x1448", square == (1448, 1448), square)
check("output_size 2MP/8 both divisible", square[0] % 8 == 0 and square[1] % 8 == 0, square)

# Случай из отчёта: 16:9 + 2 MP + 8 -> 1928x1088 как в «Выборе разрешения».
wide = mod.output_size(1920.0, 1080.0, 2.0, 8)
check("output_size 16:9 2MP/8 -> 1928x1088", wide == (1928, 1088), wide)
check("output_size keeps 16:9", abs(wide[0] / wide[1] - 16 / 9) / (16 / 9) < 0.01, wide)
check("output_size 16:9 divisible", wide[0] % 8 == 0 and wide[1] % 8 == 0, wide)

check("output_size 1MP/8 square -> 1024x1024", mod.output_size(1000, 1000, 1.0, 8) == (1024, 1024))
check("output_size 0.5MP/8 square -> 728x728", mod.output_size(1000, 1000, 0.5, 8) == (728, 728))
check("output_size step 64", mod.output_size(1600, 900, 1.0, 64) == (1344, 768), mod.output_size(1600, 900, 1.0, 64))
check("output_size step 12", mod.output_size(1600, 900, 1.0, 12) == (1368, 768), mod.output_size(1600, 900, 1.0, 12))
check("output_size step 64 tall", mod.output_size(1150, 1000, 1.0, 64) == (1088, 960), mod.output_size(1150, 1000, 1.0, 64))

# Шаг применяется как есть (диапазон 8..128 задаёт UI, как в ResolutionSelector).
low = mod.output_size(1000, 1000, 2.0, 1)
check("output_size multiplicity 1 honoured", low == (1448, 1448), low)
high = mod.output_size(1000, 1000, 2.0, 512)
check("output_size multiplicity 512 honoured", high[0] % 512 == 0 and high[1] % 512 == 0, high)
check("degenerate output_size stays positive", min(mod.output_size(0, 0, 0, 0)) >= 8)

# Ручной размер — это ТОЧНЫЙ размер результата: он не округляется до кратности.
# Иначе картинка 1001×751 получает 1000×752 и выход пересэмплируется, хотя
# пользователь ничего не обрезал (кнопка «Сбросить»/загрузка картинки).
exact = mod.output_size(1000, 1000, 2.0, 8, (1001, 751))
check("output_size manual size is exact (no multiplicity rounding)", exact == (1001, 751), exact)
exact_odd = mod.output_size(1000, 1000, 2.0, 64, (37, 29))
check("output_size manual size exact at step 64", exact_odd == (37, 29), exact_odd)

# ─────────────────────────── 5. frame_geometry ─────────────────────────────

geo = mod.frame_geometry(full, 100, 100, 100, 100)
check("full frame -> full src", geo["src_box"] == (0, 0, 100, 100), geo)
check("full frame -> full dst", geo["dst_box"] == (0, 0, 100, 100), geo)

geo = mod.frame_geometry({"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}, 100, 100, 50, 50)
check("right-bottom quarter src", geo["src_box"] == (50, 50, 100, 100), geo)
check("right-bottom quarter dst", geo["dst_box"] == (0, 0, 50, 50), geo)

geo = mod.frame_geometry({"x": -0.5, "y": -0.5, "w": 2.0, "h": 2.0}, 100, 100, 200, 200)
check("expanded frame src clamped", geo["src_box"] == (0, 0, 100, 100), geo)
check("expanded frame dst centered", geo["dst_box"] == (50, 50, 150, 150), geo)

check("no intersection -> None", mod.frame_geometry({"x": 2.0, "y": 0, "w": 0.5, "h": 1}, 100, 100, 100, 100) is None)
check("tiny frame still maps", mod.frame_geometry({"x": 0, "y": 0, "w": 0.01, "h": 0.01}, 100, 100, 8, 8)["dst_box"] == (0, 0, 8, 8))

geo = mod.frame_geometry({"x": -0.25, "y": 0.0, "w": 0.5, "h": 1.0}, 100, 100, 100, 100)
check("half-outside left src", geo["src_box"] == (0, 0, 25, 100), geo)
check("half-outside left dst", geo["dst_box"] == (50, 0, 100, 100), geo)

# Fractional frames: the visible part is grown to whole pixels (floor/ceil),
# the destination keeps the fractional offset so neighbors never gap.
frac = mod.frame_geometry({"x": 0.0333, "y": 0.0333, "w": 0.2, "h": 0.2}, 100, 100, 20, 20)
check("fractional src uses floor/ceil", frac["src_box"] == (3, 3, 24, 24), frac)
check("fractional dst offset", frac["dst_box"] == (0, 0, 20, 20), frac)

frac = mod.frame_geometry({"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}, 101, 101, 101, 101)
check("odd sizes: src box whole pixels", frac["src_box"] == (25, 25, 76, 76), frac)
# floor(fx) is left of the frame, so the destination edge clamps to 0 and the
# whole canvas is covered — no gap, no shift of the right edge.
check("odd sizes: dst covers canvas", frac["dst_box"] == (0, 0, 101, 101), frac)

# ────────────────────────── 6. render_frames ───────────────────────────────

img = make_image(1, 10, 10)
out, mask, w, h = mod.render_frames(img, None, full, "transparent", "bicubic", 0.0001, 1)
check("identity size", (w, h) == (10, 10), (w, h))
check("identity batch", out.shape == (1, 10, 10, 4), out.shape)
check("identity pixels", close(out[0, 3, 4, 0], 340.0) and close(out[0, 3, 4, 2], 342.0), out[0, 3, 4])
check("identity alpha from source", all(close(out[0, y, x, 3], 1.0) for y in range(10) for x in range(10)))
check("identity mask ones", all(close(mask[0, y, x], 1.0) for y in range(10) for x in range(10)))

# Crop a quarter: values must come from the bottom-right quadrant.
half = {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}
out, mask, w, h = mod.render_frames(make_image(1, 20, 20), None, half, "transparent", "bicubic", 0.0001, 1)
check("quarter size", (w, h) == (10, 10), (w, h))
check("quarter first pixel from (10,10)", close(out[0, 0, 0, 0], 1100.0), out[0, 0, 0])
check("quarter last pixel from (19,19)", close(out[0, 9, 9, 0], 2090.0), out[0, 9, 9])

# Outpainting: transparent fill -> серый RGB (0.5) и нулевая альфа.
# Раньше RGB был нулём, и превью без альфы показывало аутпейнт чёрным.
pad = {"x": -0.5, "y": -0.5, "w": 2.0, "h": 2.0}
out, mask, w, h = mod.render_frames(make_image(1, 10, 10), None, pad, "transparent", "bicubic", 0.0004, 1)
check("pad size", (w, h) == (20, 20), (w, h))
check("pad corner rgb gray", close(out[0, 0, 0, 0], 0.5) and close(out[0, 0, 0, 2], 0.5), out[0, 0, 0])
check("pad corner alpha zero", close(out[0, 0, 0, 3], 0.0))
check("pad top-left is fill", close(out[0, 0, 0, 0], 0.5) and close(out[0, 4, 4, 0], 0.5))
check("pad source starts at dst(5,5)", close(out[0, 5, 5, 0], 0.0) and close(out[0, 14, 14, 0], 990.0), out[0, 14, 14])
check("pad mask zero outside", close(mask[0, 0, 0], 0.0) and close(mask[0, 10, 10], 1.0), mask[0, 10, 10])
check(
    "заливка transparent: цвет серый, альфа нулевая",
    mod._fill_color("transparent", 4) == (0.5, 0.5, 0.5, 0.0),
    mod._fill_color("transparent", 4),
)
check(
    "заливка gray: цвет серый, альфа единичная",
    mod._fill_color("gray", 4) == (0.5, 0.5, 0.5, 1.0),
    mod._fill_color("gray", 4),
)

# Opaque fill: alpha 1, mask 1, chosen color outside.
out, mask, w, h = mod.render_frames(make_image(1, 10, 10), None, pad, "black", "bicubic", 0.0004, 1)
check("opaque fill on rgb source stays 3ch", out.shape[-1] == 3, out.shape)
check("black fill rgb", close(out[0, 0, 0, 0], 0) and close(out[0, 0, 0, 1], 0))
check("black fill mask", close(mask[0, 0, 0], 1.0) and close(mask[0, 10, 10], 1.0))
# With an alpha channel (mask input) the fill is opaque too.
out, mask, w, h = mod.render_frames(make_image(1, 10, 10), T((1, 10, 10), [0.25] * 100), pad, "black", "bicubic", 0.0004, 1)
check("black fill alpha", close(out[0, 0, 0, 3], 1.0))
check(
    "маска 0.25 = скрыть четверть (видимая альфа 0.75)",
    close(out[0, 10, 10, 3], 0.75),
    out[0, 10, 10].data,
)

# ── Маска ComfyUI ───────────────────────────────────────────────────────────
# В ComfyUI маска = 1 там, где область надо УБРАТЬ (LoadImage отдаёт 1 - alpha).
# Значит видимая альфа = 1 - маска, а сама маска живёт в координатах
# ИСТОЧНИКА и вырезается по области кадра (её нельзя сжимать в область).

# Маска нулевая = ничего не скрывать (так отдаёт LoadImage для картинки без
# альфы — заглушка 64×64 нулей). Раньше маска трактовалась как альфа, и весь
# кадр становился прозрачным (чёрный выход).
zero_mask = T((1, 10, 10), [0.0] * 100)
out, mask, w, h = mod.render_frames(make_image(1, 10, 10), zero_mask, full, "transparent", "bicubic", 0.0001, 1)
check("маска нулевая = ничего не скрывать (альфа 1)", close(out[0, 2, 2, 3], 1.0), out[0, 2, 2, 3])
check("маска нулевая: RGB не тронут", close(out[0, 2, 2, 0], 220.0), out[0, 2, 2, 0])

one_mask = T((1, 10, 10), [1.0] * 100)
out, mask, w, h = mod.render_frames(make_image(1, 10, 10), one_mask, full, "transparent", "bicubic", 0.0001, 1)
check("маска единичная = скрыть всё (альфа 0)", close(out[0, 2, 2, 3], 0.0), out[0, 2, 2, 3])

half_mask = T((1, 10, 10), [0.5] * 100)
out, mask, w, h = mod.render_frames(make_image(1, 10, 10), half_mask, full, "transparent", "bicubic", 0.0001, 1)
check("маска 0.5 = 半 полупрозрачно (альфа 0.5)", close(out[0, 2, 2, 3], 0.5), out[0, 2, 2, 3])

# Маска вырезается по области кадра, а не сжимается в неё.
big_mask = T((1, 20, 20), [0.0] * 400)
for _y in range(10):
    for _x in range(10):
        big_mask.data[_y * 20 + _x] = 1.0  # верхний левый квадрант скрыт
out, mask, w, h = mod.render_frames(make_image(1, 20, 20), big_mask, half, "transparent", "bicubic", 0.0001, 1)
check(
    "маска вырезается по области кадра (нижний правый квадрант не скрыт)",
    close(out[0, 2, 2, 3], 1.0),
    out[0, 2, 2].data,
)
# Пиксель, где «вырезать» и «сжать в область» дают разный результат:
# вырезанная маска в правом нижнем квадранте = 0 (не скрыто), сжатая = 1.
check(
    "маска вырезается, а не сжимается в область кадра",
    close(out[0, 7, 7, 3], 1.0),
    out[0, 7, 7].data,
)

# Заглушка другого размера (LoadImage: 64×64 для картинки без альфы) приводится
# к размеру источника и ничего не скрывает.
stub_mask = T((1, 64, 64), [0.0] * 4096)
out, mask, w, h = mod.render_frames(make_image(1, 20, 20), stub_mask, half, "transparent", "bicubic", 0.0001, 1)
check(
    "маска-заглушка 64×64 = ничего не скрывать",
    close(out[0, 2, 2, 3], 1.0),
    out[0, 2, 2].data,
)

# RGB source + opaque fill stays 3-channel.
out, mask, w, h = mod.render_frames(make_image(1, 10, 10), None, full, "white", "bicubic", 0.0001, 1)
check("rgb source -> 3 channels", out.shape[-1] == 3, out.shape)

# Сброс к изображению: полный кадр + ручной размер = размер источника ⇒
# выход обязан совпасть с источником пиксель в пиксель (ресайза быть не должно).
src = make_image(1, 29, 37)
out, mask, w, h = mod.render_frames(src, None, full, "transparent", "lanczos", 2.0, 8, (37, 29))
check("full frame + exact manual keeps size", (w, h) == (37, 29), (w, h))
check("full frame + exact manual shape", out.shape[:3] == (1, 29, 37), out.shape)
check(
    "full frame + exact manual keeps pixels",
    all(
        close(out[0, y, x, c], src[0, y, x, c])
        for y in range(29)
        for x in range(37)
        for c in range(3)
    ),
    "pixels differ",
)

# RGB source + transparent fill gains the alpha channel.
out, mask, w, h = mod.render_frames(make_image(1, 10, 10), None, full, "transparent", "bicubic", 0.0001, 1)
check("transparent adds alpha", out.shape[-1] == 4, out.shape)

# Batch: two frames, same output size, each keeps its own content.
batch = make_image(2, 10, 10)
out, mask, w, h = mod.render_frames(batch, None, full, "transparent", "bicubic", 0.0001, 1)
check("batch output batch=2", out.shape == (2, 10, 10, 4), out.shape)
check("batch frame 0 value", close(out[0, 1, 1, 0], 110.0), out[0, 1, 1, 0])
check("batch frame 1 value", close(out[1, 1, 1, 0], 10110.0), out[1, 1, 1, 0])

# Nearest-exact path also works through the stub.
out, mask, w, h = mod.render_frames(make_image(1, 10, 10), None, half, "transparent", "nearest-exact", 0.000025, 1)
check("nearest-exact path", out.shape == (1, 10, 10, 4) and close(out[0, 0, 0, 0], 550.0), out.shape)

# Batch masks: each frame must take its OWN mask frame, not the first one.
# Маска ComfyUI: 1 = убрать, поэтому видимая альфа = 1 - маска.
masks = T((2, 10, 10), [0.25] * 100 + [0.75] * 100)
out, mask, w, h = mod.render_frames(make_image(2, 10, 10), masks, full, "transparent", "bicubic", 0.0001, 1)
check("batch mask frame 0", close(out[0, 5, 5, 3], 0.75), out[0, 5, 5].data)
check("batch mask frame 1", close(out[1, 5, 5, 3], 0.25), out[1, 5, 5].data)

# ────────────────────────────── 7. node API ────────────────────────────────

node = mod.DeggCropImage()
result = node.crop(
    make_image(1, 10, 10),
    aspect_ratio="1:1",
    lock_ratio=True,
    dim_outside=50,
    multiplicity=1,
    megapixels=0.0001,
    resample="bilinear",
    fill="transparent",
    crop_rect='{"x":0.5,"y":0.5,"w":0.5,"h":0.5}',
)
check("crop returns 4 values", len(result) == 4)
check("crop width/height are ints", isinstance(result[2], int) and isinstance(result[3], int), (result[2], result[3]))
check("crop size from rect", (result[2], result[3]) == (10, 10), (result[2], result[3]))
check("crop image shape", result[0].shape == (1, 10, 10, 4), result[0].shape)
check("crop mask shape", result[1].shape == (1, 10, 10), result[1].shape)

bad = node.crop(
    make_image(1, 8, 8),
    multiplicity=2,
    megapixels=0.000064,
    resample="unknown",
    fill="rainbow",
    crop_rect="not json",
)
check("unknown enum values fall back", bad[0].shape == (1, 10, 10, 4), bad[0].shape)
check("bad ret fields ints", isinstance(bad[2], int) and isinstance(bad[3], int))

types = mod.DeggCropImage.INPUT_TYPES()
check("crop_rect is hidden STRING", types["optional"]["crop_rect"][0] == "STRING" and types["optional"]["crop_rect"][1].get("hidden") is True)
check("mask is optional MASK", types["optional"]["mask"][0] == "MASK")
check("lanczos is the default resample", types["required"]["resample"][1]["default"] == "lanczos")
def _opt(name):
    """Интерфейсные входы объявлены необязательными (см. контракт ниже)."""
    return types["optional"][name][1]


check("lock_ratio default on", _opt("lock_ratio")["default"] is True)
_lock = _opt("lock_ratio")
check("lock_ratio подписи EN-источник", _lock.get("label_on") == "Locked" and _lock.get("label_off") == "Free", _lock)
_cx = _opt("center_x")
_cy = _opt("center_y")
check(
    "frame_w/frame_h — INT (размер рамки в пикселях)",
    types["optional"]["frame_w"][0] == "INT"
    and types["optional"]["frame_h"][0] == "INT"
    and _opt("frame_w")["step"] == 1
    and _opt("frame_h")["step"] == 1
    and _opt("frame_w")["min"] == 0
    and _opt("frame_h")["min"] == 0,
    {"frame_w": _opt("frame_w"), "frame_h": _opt("frame_h")},
)
check(
    "output_size — скрытый STRING (ручной размер результата)",
    types["optional"]["output_size"][0] == "STRING"
    and types["optional"]["output_size"][1].get("hidden") is True,
    types["optional"]["output_size"],
)
check(
    "crop_rect — скрытый STRING (состояние кадра)",
    types["optional"]["crop_rect"][0] == "STRING"
    and types["optional"]["crop_rect"][1].get("hidden") is True,
    types["optional"]["crop_rect"],
)
check(
    "aspect_ratio — COMBO среди необязательных",
    types["optional"]["aspect_ratio"][0] == ["free", "1:1", "2:3", "3:2", "4:3", "3:4", "16:9", "9:16", "21:9"],
    types["optional"]["aspect_ratio"][0],
)
check(
    "center_x/center_y — INT со знаком (смещение в пикселях)",
    types["optional"]["center_x"][0] == "INT"
    and types["optional"]["center_y"][0] == "INT"
    and _cx["min"] < 0
    and _cy["min"] < 0
    and _cx["step"] == 1
    and _cy["step"] == 1,
    {"center_x": _cx, "center_y": _cy},
)
# Python — EN-источник интерфейса: русский живёт в locales/. Проверяем ТОЛЬКО
# объявленные тексты (типы, tooltip, description) — не список файлов папки
# `input`: имена файлов даёт пользователь, и кириллица в них роняла проверку.
def _ui_text(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _ui_text(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _ui_text(item)]
    return []


def _ru(text):
    return any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in text)


_ui_strings = []
for _group in types.values():
    for _name, _spec in _group.items():
        if _name == "image_file":
            continue  # список файлов пользователя, а не текст интерфейса
        _ui_strings.append(_name)
        if len(_spec) > 1 and isinstance(_spec[1], dict):
            _opts = {k: v for k, v in _spec[1].items() if k != "default"}
            _ui_strings.extend(_ui_text(_opts))
_ui_strings.append(str(getattr(mod.DeggCropImage, "DESCRIPTION", "")))
_ru_ui = [text for text in _ui_strings if _ru(text)]
check("Python без русского текста в объявленном интерфейсе", not _ru_ui, _ru_ui[:3])
check("category My_custom_nodes/Image", mod.DeggCropImage.CATEGORY == "My_custom_nodes/Image")
# Интерфейсные входы объявлены НЕОБЯЗАТЕЛЬНЫМИ: сервер их не читает, и API
# не должен требовать их передавать. (Сокет image и выбор файла image_file
# вынесены из этого списка: image — optional, а image_file обязан быть
# required, иначе фронтенд не покажет кнопку загрузки файла.)
# Порядок виджетов на теле ноды задаёт
# редактор (WIDGET_ORDER в web/js/crop_image.js) — он проверяется аудитом и
# живой DOM-пробой.
INTERFACE_INPUTS = [
    "mask_file",
    "lock_ratio",
    "center_x",
    "center_y",
    "frame_w",
    "frame_h",
    "aspect_ratio",
    "golden_ratio",
]
check(
    "интерфейсные входы объявлены необязательными",
    all(name in types["optional"] for name in INTERFACE_INPUTS)
    and not any(name in types["required"] for name in INTERFACE_INPUTS),
    {"optional": list(types["optional"]), "required": list(types["required"])},
)
check(
    "golden_ratio — переключатель сетки, по умолчанию выключен",
    types["optional"]["golden_ratio"][0] == "BOOLEAN"
    and types["optional"]["golden_ratio"][1]["default"] is False,
    types["optional"]["golden_ratio"],
)
# ComfyUI передаёт все входы как именованные аргументы crop() — любой вход
# без параметра в сигнатуре роняет ноду при запуске (TypeError).
import inspect as _inspect

_params = set(_inspect.signature(mod.DeggCropImage.crop).parameters)
_declared = set(types["required"]) | set(types["optional"])
check(
    "все входы объявлены в сигнатуре crop()",
    not (_declared - _params),
    sorted(_declared - _params),
)
check(
    "лишних параметров в crop() нет",
    not (_params - _declared - {"self"}),
    sorted(_params - _declared - {"self"}),
)
check(
    "режим файла: сокет image и файл — необязательные входы",
    # сокет image — optional (нода работает и без подключения)
    "image" in types["optional"]
    and types["optional"]["image"][0] == "IMAGE"
    # файл — необязательный комбо: сервер проверяет значение комбо по
    # списку, и обязательный вход заставил бы API подставлять имя файла
    # даже при подключённом сокете. Кнопку загрузки файла фронтенд рисует
    # прямо в комбо (image_upload), отдельный вход «upload» не нужен.
    and "image_file" in types["optional"]
    and isinstance(types["optional"]["image_file"][0], list)
    and types["optional"]["image_file"][1].get("image_upload") is True
    and "upload" not in types["required"]
    and "upload" not in types["optional"],
    {
        "required": list(types["required"]),
        "optional": list(types["optional"]),
        "image_file_options": types["optional"]["image_file"][1],
    },
)
check(
    "вход mask_file (маска из штатного Mask Editor) — необязательный и скрытый",
    "mask_file" in types["optional"]
    and types["optional"]["mask_file"][0] == "STRING"
    and types["optional"]["mask_file"][1].get("hidden") is True,
    types["optional"].get("mask_file"),
)
check(
    "мегапиксели и прочее остались обязательными",
    all(
        name in types["required"]
        for name in ("megapixels", "multiplicity", "resample", "fill", "dim_outside")
    ),
    list(types["required"]),
)
check(
    "вход mask остался необязательным",
    "mask" in types["optional"],
    list(types["optional"]),
)
check("RETURN_TYPES", mod.DeggCropImage.RETURN_TYPES == ("IMAGE", "MASK", "INT", "INT"))

# Пустой результат 8×8 (= multiplicity) появлялся, когда в input `image`
# приезжала СТРОКА: фронтенд кладёт в промпт значения виджетов ПО ИМЕНИ, а
# служебный виджет штатного Mask Editor называется `image` (имя пересекается
# с сокетом IMAGE). Строка — не картинка: источник не подан, нода обязана
# прочитать файл image_file, а не отдавать пустую картинку.
_orig_loader = mod.load_source_file
mod.load_source_file = lambda name: (
    (make_image(1, 20, 20), None) if str(name) == "photo.png" else (None, None)
)
try:
    _crop = mod.DeggCropImage()
    out, mask, w, h = _crop.crop(
        image="photo.png",
        image_file="photo.png",
        megapixels=0.0004,
        multiplicity=8,
        crop_rect='{"x": 0, "y": 0, "w": 1, "h": 1}',
    )
finally:
    mod.load_source_file = _orig_loader

_exp_w, _exp_h = mod.output_size(20, 20, 0.0004, 8)
check(
    "строка во входе image не считается источником: читается файл",
    (w, h) == (_exp_w, _exp_h),
    {"got": (w, h), "expected": (_exp_w, _exp_h), "empty_fallback": (8, 8)},
)
check(
    "строка во входе image: размер результата не равен пустой заглушке 8×8",
    not (w == 8 and h == 8),
    (w, h),
)
check("mapping registered", mod.NODE_CLASS_MAPPINGS.get("DeggCropImage") is mod.DeggCropImage)


# ── Штатный Mask Editor: Paint-слой обязан попасть в результат ─────────────
# Редактор сохраняет ОДИН файл — «=clipspace-painted-masked-<ts>.png»:
#   RGB   = исходная картинка + слой Paint (цветная кисть);
#   альфа = 255 - маска  ⇒  маска = 1 - альфа (конвенция ComfyUI).
# Значит сервер обязан взять из этого файла И картинку, И маску — ровно так,
# как это делает LoadImage. Иначе покраска цветной кистью теряется: пользователь
# рисует, сохраняет, а на выходе исходная картинка.
def _painted_image():
    data = []
    for _y in range(4):
        for x in range(4):
            data += [1.0, 0.0, 0.0] if x < 2 else [0.5, 0.5, 0.5]
    return T((1, 4, 4, 3), data)


def _mask_columns(left: bool):
    data = []
    for _y in range(4):
        for x in range(4):
            data.append(1.0 if (left and x < 2) else 0.0)
    return T((1, 4, 4), data)


def _editor_loader(mask_tensor):
    painted = _painted_image()

    def loader(name):
        text = str(name)
        if text == "photo.png":
            return make_image(1, 4, 4), None
        if text.startswith("clipspace-painted-masked-"):
            return painted, mask_tensor
        return None, None

    return loader


_editor_node = mod.DeggCropImage()


def _run_editor(mask_tensor):
    _orig = mod.load_source_file
    mod.load_source_file = _editor_loader(mask_tensor)
    try:
        return _editor_node.crop(
            image_file="photo.png",
            mask_file="clipspace-painted-masked-1.png [input]",
            crop_rect='{"x": 0, "y": 0, "w": 1, "h": 1}',
            output_size="4,4",
            multiplicity=8,
            fill="transparent",
        )
    finally:
        mod.load_source_file = _orig


_out, _mask, _ow, _oh = _run_editor(_mask_columns(left=False))
check(
    "Mask Editor: Paint-слой (цветная кисть) попадает в результат",
    close(_out[0, 0, 0, 0], 1.0) and close(_out[0, 0, 0, 1], 0.0),
    {"left_pixel_rgb": [_out[0, 0, 0, c] for c in range(3)], "size": (_ow, _oh)},
)
check(
    "Mask Editor: пустая маска ничего не вырезает (альфа = 1)",
    all(close(_out[0, y, x, 3], 1.0) for y in range(4) for x in range(4)),
    {"alpha": [[_out[0, y, x, 3] for x in range(4)] for y in range(4)]},
)

_out2, _mask2, _ow2, _oh2 = _run_editor(_mask_columns(left=True))
check(
    "Mask Editor: нарисованная маска вырезает область (альфа = 0 слева)",
    close(_out2[0, 0, 0, 3], 0.0) and close(_out2[0, 0, 3, 3], 1.0),
    {"alpha_row": [_out2[0, 0, x, 3] for x in range(4)]},
)
# ── Картинка по КАБЕЛЮ + покраска: Paint-слой обязан выйти ─────────────────
# Жалоба (2026-10-23): подключил картинку проводом, нарисовал кистью, сохранил
# (превью показывает покраску), запустил — на выходе картинка БЕЗ покраски.
# Причина: при поданном сокете сервер брал только маску из файла редактора, а
# картинку — из сокета, поэтому слой Paint (он живёт в RGB файла) терялся.
# Файл редактора = ЭТА ЖЕ картинка + Paint, поэтому при совпадении основы он
# и должен быть источником картинки. Если основа ДРУГАЯ (файл от прошлой
# картинки), побеждает сокет — иначе на выходе чужая картинка.
def _painted_from(base, stroke=None):
    """Копия картинки с нарисованным пятном (как файл редактора)."""
    data = list(base.data)
    if stroke is not None:
        y, x, rgb = stroke
        start = ((0 * base.shape[1] + y) * base.shape[2] + x) * base.shape[3]
        for c, value in enumerate(rgb):
            data[start + c] = float(value)
    return T(base.shape, data)


_BASE = make_image(1, 4, 4)
_PAINTED = _painted_from(_BASE, stroke=(1, 1, (1.0, 0.0, 0.0)))


def _wire_loader(base):
    def loader(name):
        text = str(name)
        if text == "photo.png":
            return base, None
        if text.startswith("clipspace-painted-masked-"):
            return _PAINTED, _mask_columns(left=False)
        return None, None

    return loader


def _run_wire(base, painted_name="clipspace-painted-masked-1.png [input]"):
    _orig = mod.load_source_file
    mod.load_source_file = _wire_loader(base)
    try:
        return _editor_node.crop(
            image=base,
            image_file="photo.png",
            mask_file=painted_name,
            crop_rect='{"x": 0, "y": 0, "w": 1, "h": 1}',
            output_size="4,4",
            multiplicity=8,
            fill="transparent",
        )
    finally:
        mod.load_source_file = _orig


_wire_out, _wire_mask, _ww, _wh = _run_wire(_BASE)
check(
    "Mask Editor: картинка по кабелю — Paint-слой попадает в результат",
    close(_wire_out[0, 1, 1, 0], 1.0)
    and close(_wire_out[0, 1, 1, 1], 0.0)
    and close(_wire_out[0, 0, 0, 0], _BASE[0, 0, 0, 0]),
    {"stroke_pixel": [_wire_out[0, 1, 1, c] for c in range(3)]},
)

_OTHER = T(_BASE.shape, [0.25] * (4 * 4 * 3))
_stale_out, _stale_mask, _stw, _sth = _run_wire(_OTHER)
check(
    "Mask Editor: файл от ДРУГОЙ картинки не подменяет источник из кабеля",
    close(_stale_out[0, 0, 0, 0], 0.25) and close(_stale_out[0, 1, 1, 0], 0.25),
    {"pixel": [_stale_out[0, 0, 0, c] for c in range(3)]},
)

check(
    "Mask Editor: Paint-слой сохраняется и в месте, оставленном маской",
    close(_out2[0, 3, 3, 0], 0.5),
    {"right_pixel_rgb": [_out2[0, 3, 3, c] for c in range(3)]},
)

# ──────────── 8. ФАЙЛЫ: границы input, EXIF, кратность, валидация ──────────
# Нода читает картинку по ИМЕНИ из input (как LoadImage). Три обязательных
# свойства: имя не выводит за пределы input (path traversal), поворот EXIF
# применён к пикселям (иначе рамка не совпадает с тем, что видно), а служебные
# входы не падают на мусоре. Проверяется на реальных файлах с заглушками
# PIL/folder_paths: прибор обязан различать вызовы, а не подтверждать сам себя.

import os
import tempfile
import time
import types as _types


class _Arr:
    """numpy-заглушка: astype/деление/вычитание сохраняют вложенные списки."""

    def __init__(self, data):
        self.data = data

    def astype(self, dtype):
        return self

    def __truediv__(self, other):
        return _Arr(_map_num(self.data, lambda v: v / float(other)))

    def __rsub__(self, other):
        return _Arr(_map_num(self.data, lambda v: float(other) - v))


def _map_num(node, fn):
    if isinstance(node, list):
        return [_map_num(item, fn) for item in node]
    return fn(node)


def _from_numpy(value):
    """torch.from_numpy: вложенные списки -> T (как настоящий torch)."""
    data = getattr(value, "data", value)
    shape = []
    node = data
    while isinstance(node, list):
        shape.append(len(node))
        node = node[0] if node else None
    flat = []

    def walk(item):
        if isinstance(item, list):
            for sub in item:
                walk(sub)
        else:
            flat.append(float(item))

    walk(data)
    return T(tuple(shape), flat)


sys.modules["torch"].from_numpy = _from_numpy

_IO_BASE = Path(tempfile.mkdtemp(prefix="degg_io_"))
_IO_INPUT = _IO_BASE / "input"
_IO_INPUT.mkdir()
(_IO_BASE / "secret.png").write_bytes(b"outside the input folder")
(_IO_INPUT / "ok.png").write_bytes(b"exif=1")
(_IO_INPUT / "rot.jpg").write_bytes(b"exif=6")
_IO_OPENED = []
_IO_TRANSPOSED = []


class _IOHandle:
    """PIL-заглушка: пиксели 40x20, поворот по метке в файле (exif=6)."""

    def __init__(self, path):
        self.path = path
        body = path.read_text(errors="ignore")
        self.orientation = 6 if "exif=6" in body else 1
        self.size = (20, 40)  # ширина, высота — как у PIL
        self.pixels = [[[0.5, 0.25, 0.75] for _ in range(20)] for _ in range(40)]
        self.converted = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def convert(self, mode):
        self.converted = mode
        return self

    def getbands(self):
        return ("R", "G", "B")

    def getchannel(self, name):
        return _IOChannel()


class _IOChannel:
    pixels = [[0.0] * 20 for _ in range(40)]


def _io_open(path):
    target = Path(str(path))
    if not target.is_file():
        raise FileNotFoundError(str(target))
    _IO_OPENED.append(str(target))
    return _IOHandle(target)


def _io_exif_transpose(img):
    _IO_TRANSPOSED.append(img.path)
    if img.orientation in (5, 6, 7, 8):
        img.pixels = [list(col) for col in zip(*img.pixels)]
        img.size = (img.size[1], img.size[0])
    return img


_np_mod = _types.ModuleType("numpy")
_np_mod.float32 = "float32"
_np_mod.array = lambda obj: _Arr(getattr(obj, "pixels", obj))
sys.modules["numpy"] = _np_mod

_pil_mod = _types.ModuleType("PIL")
_pil_image = _types.ModuleType("PIL.Image")
_pil_image.open = _io_open
_pil_ops = _types.ModuleType("PIL.ImageOps")
_pil_ops.exif_transpose = _io_exif_transpose
_pil_mod.Image = _pil_image
_pil_mod.ImageOps = _pil_ops
sys.modules["PIL"] = _pil_mod
sys.modules["PIL.Image"] = _pil_image
sys.modules["PIL.ImageOps"] = _pil_ops

_fp_mod = _types.ModuleType("folder_paths")
_fp_mod.get_input_directory = lambda: str(_IO_INPUT)
_fp_mod.filter_files_content_types = lambda files, types_: files
sys.modules["folder_paths"] = _fp_mod

_io_img, _io_mask = mod.load_source_file("ok.png")
check(
    "файл из input читается по имени",
    _io_img is not None and _io_img.shape == (1, 40, 20, 3),
    None if _io_img is None else _io_img.shape,
)

_IO_OPENED.clear()
_trav_img, _trav_mask = mod.load_source_file("../secret.png")
check(
    "path traversal: '../' за пределы input не читается",
    _trav_img is None and _trav_mask is None,
    None if _trav_img is None else "файл вне input прочитан",
)
check(
    "path traversal: файл вне input не открывался",
    not any("secret.png" in item for item in _IO_OPENED),
    _IO_OPENED,
)

_abs_out = str(_IO_BASE / "secret.png")
_abs_img, _ = mod.load_source_file(_abs_out)
check(
    "path traversal: абсолютный путь вне input отклонён",
    _abs_img is None,
    _abs_out if _abs_img is not None else "",
)

_abs_in = str(_IO_INPUT / "ok.png")
_abs_in_img, _ = mod.load_source_file(_abs_in)
check(
    "абсолютный путь ВНУТРИ input разрешён (старые воркфлоу)",
    _abs_in_img is not None,
    _abs_in,
)

_IO_TRANSPOSED.clear()
_rot_img, _ = mod.load_source_file("rot.jpg")
check("EXIF: поворот применяется (ImageOps.exif_transpose)", len(_IO_TRANSPOSED) == 1, len(_IO_TRANSPOSED))
check(
    "EXIF: пиксели повёрнуты (40x20 -> 20x40, как показал бы браузер)",
    _rot_img is not None and _rot_img.shape == (1, 20, 40, 3),
    None if _rot_img is None else _rot_img.shape,
)

_missing_img, _missing_mask = mod.load_source_file("нет-такого.png")
check("отсутствующий файл -> пусто", _missing_img is None and _missing_mask is None)

# Пустой результат: кратность не должна ронять ноду (torch: negative dim).
for _mult, _want in ((-8, 1), (0, 1)):
    try:
        _empty_img, _empty_mask, _ew, _eh = mod.DeggCropImage().crop(
            image=None, image_file="", mask_file="", multiplicity=_mult
        )
        _ok_empty = (
            _empty_img.shape == (1, _want, _want, 3)
            and _empty_mask.shape == (1, _want, _want)
            and (_ew, _eh) == (0, 0)
        )
        _detail = {"image": _empty_img.shape, "mask": _empty_mask.shape}
    except Exception as exc:  # noqa: BLE001 - падение и есть дефект
        _ok_empty = False
        _detail = f"{type(exc).__name__}: {exc}"
    check(f"пустой результат: multiplicity={_mult} не падает", _ok_empty, _detail)

# image списком (батч приходит как list) — это поданная картинка, а не повод
# молча взять файл: иначе на выходе чужое изображение.
_list_img, _, _lw, _lh = mod.DeggCropImage().crop(
    image=[make_image(1, 4, 4)],
    image_file="ok.png",
    mask_file="",
    crop_rect='{"x": 0, "y": 0, "w": 1, "h": 1}',
    output_size="4,4",
    multiplicity=8,
    megapixels=2.0,
    fill="transparent",
)
_direct_img, _, _dw, _dh = mod.DeggCropImage().crop(
    image=make_image(1, 4, 4),
    image_file="ok.png",
    mask_file="",
    crop_rect='{"x": 0, "y": 0, "w": 1, "h": 1}',
    output_size="4,4",
    multiplicity=8,
    megapixels=2.0,
    fill="transparent",
)
check(
    "image списком: берётся картинка из списка, а не файл",
    (_lw, _lh) == (_dw, _dh) and close(_list_img[0, 0, 0, 0], _direct_img[0, 0, 0, 0]),
    {"list": (_lw, _lh), "direct": (_dw, _dh)},
)

# Валидация входов: битый файл виден до запуска, пустое значение законно.
check(
    "VALIDATE_INPUTS: пустые значения проходят",
    mod.DeggCropImage.VALIDATE_INPUTS(image_file="", mask_file="") is True,
)
check(
    "VALIDATE_INPUTS: существующий файл проходит",
    mod.DeggCropImage.VALIDATE_INPUTS(image_file="ok.png", mask_file="rot.jpg [input]") is True,
)
_bad = mod.DeggCropImage.VALIDATE_INPUTS(image_file="ok.png", mask_file="удалён.png")
check(
    "VALIDATE_INPUTS: отсутствующий файл -> сообщение об ошибке",
    isinstance(_bad, str) and "удалён.png" in _bad,
    _bad,
)
_bad_out = mod.DeggCropImage.VALIDATE_INPUTS(image_file="../secret.png", mask_file="")
check(
    "VALIDATE_INPUTS: путь вне input -> ошибка",
    isinstance(_bad_out, str),
    _bad_out,
)

# Кэш: перезапись файла с ТЕМ ЖЕ именем обязана пересчитать ноду (как LoadImage).
_stamp_a = mod.DeggCropImage.IS_CHANGED(image_file="ok.png", mask_file="")
_stamp_b = mod.DeggCropImage.IS_CHANGED(image_file="ok.png", mask_file="")
check("IS_CHANGED: без изменений штамп совпадает", _stamp_a == _stamp_b, (_stamp_a, _stamp_b))
_ok_path = _IO_INPUT / "ok.png"
_ok_path.write_bytes(b"exif=1 reuploaded")
os.utime(_ok_path, (time.time() + 5, time.time() + 5))
_stamp_c = mod.DeggCropImage.IS_CHANGED(image_file="ok.png", mask_file="")
check("IS_CHANGED: перезапись файла меняет штамп", _stamp_c != _stamp_a, (_stamp_a, _stamp_c))
_stamp_d = mod.DeggCropImage.IS_CHANGED(image_file="", mask_file="")
check(
    "IS_CHANGED: без файлов штамп стабилен (кэш по сокету не ломается)",
    _stamp_d == mod.DeggCropImage.IS_CHANGED(image_file="", mask_file=""),
    _stamp_d,
)

# ─────────────────────────────── summary ───────────────────────────────────

_p("")
_p(f"PASS: {len(oks)}  FAIL: {len(fails)}")
if fails:
    _p("failed: " + ", ".join(fails))
else:
    _p("ТЕСТ ПРОЙДЕН")
sys.exit(1 if fails else 0)
