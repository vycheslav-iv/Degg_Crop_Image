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
check("crop_rect is hidden STRING", types["required"]["crop_rect"][0] == "STRING" and types["required"]["crop_rect"][1].get("hidden") is True)
check("mask is optional MASK", types["optional"]["mask"][0] == "MASK")
check("lanczos is the default resample", types["required"]["resample"][1]["default"] == "lanczos")
check("lock_ratio default on", types["required"]["lock_ratio"][1]["default"] is True)
_lock = types["required"]["lock_ratio"][1]
check("lock_ratio подписи EN-источник", _lock.get("label_on") == "Locked" and _lock.get("label_off") == "Free", _lock)
_cx = types["required"]["center_x"][1]
_cy = types["required"]["center_y"][1]
check(
    "frame_w/frame_h — INT (размер рамки в пикселях)",
    types["required"]["frame_w"][0] == "INT"
    and types["required"]["frame_h"][0] == "INT"
    and types["required"]["frame_w"][1]["step"] == 1
    and types["required"]["frame_h"][1]["step"] == 1
    and types["required"]["frame_w"][1]["min"] == 0
    and types["required"]["frame_h"][1]["min"] == 0,
    {"frame_w": types["required"]["frame_w"][1], "frame_h": types["required"]["frame_h"][1]},
)
check(
    "output_size — скрытый STRING (ручной размер результата)",
    types["required"]["output_size"][0] == "STRING"
    and types["required"]["output_size"][1].get("hidden") is True,
    types["required"]["output_size"],
)
check(
    "center_x/center_y — INT со знаком (смещение в пикселях)",
    types["required"]["center_x"][0] == "INT"
    and types["required"]["center_y"][0] == "INT"
    and _cx["min"] < 0
    and _cy["min"] < 0
    and _cx["step"] == 1
    and _cy["step"] == 1,
    {"center_x": _cx, "center_y": _cy},
)
check(
    "Python без русского текста",
    not any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in str(types)) ,
)
check("category My_custom_nodes/Image", mod.DeggCropImage.CATEGORY == "My_custom_nodes/Image")
# Порядок виджетов на теле ноды = порядок ключей INPUT_TYPES.
EXPECTED_WIDGET_ORDER = [
    "lock_ratio",
    "center_x",
    "center_y",
    "frame_w",
    "frame_h",
    "aspect_ratio",
    "megapixels",
    "multiplicity",
    "resample",
    "fill",
    "dim_outside",
]
order = [k for k in types["required"] if k not in ("image", "crop_rect", "output_size")]
check("порядок виджетов", order == EXPECTED_WIDGET_ORDER, order)
check("RETURN_TYPES", mod.DeggCropImage.RETURN_TYPES == ("IMAGE", "MASK", "INT", "INT"))
check("mapping registered", mod.NODE_CLASS_MAPPINGS.get("DeggCropImage") is mod.DeggCropImage)

# ─────────────────────────────── summary ───────────────────────────────────

_p("")
_p(f"PASS: {len(oks)}  FAIL: {len(fails)}")
if fails:
    _p("failed: " + ", ".join(fails))
else:
    _p("ТЕСТ ПРОЙДЕН")
sys.exit(1 if fails else 0)
