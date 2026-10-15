"""
cl_squiggle.py -- render a grayscale bitmap as ONE continuous "squiggle" line.

The image is scanned in rows.  Each row is a sine wave whose amplitude and
frequency grow with the darkness of the pixels underneath, and rows are
walked boustrophedon (left->right, then right->left) so the whole picture is
a single polyline with no pen lift and no bridges.  This is the classic
engraving-style look for plotting photographs; it renders *tone*, which a
threshold trace cannot.

Standard library only.  Pillow is used when available (fast resize, JPEG
support); PNG files are decoded with a built-in reader when it is not.
"""

import base64
import math
import struct
import zlib


# ---------------------------------------------------------------------------
# Grayscale image container + loaders
# ---------------------------------------------------------------------------

class GrayImage:
    """8-bit grayscale raster, row-major ``bytearray`` of width*height."""

    def __init__(self, width, height, pixels):
        if len(pixels) != width * height:
            raise ValueError("pixel buffer size does not match %dx%d" % (width, height))
        self.width = width
        self.height = height
        self.pixels = pixels

    # -- loading ------------------------------------------------------------
    @classmethod
    def from_bytes(cls, data):
        """Decode PNG/JPEG/... bytes.  Pillow if present, else PNG only."""
        try:
            from PIL import Image  # noqa: WPS433
            import io
            img = Image.open(io.BytesIO(data))
            img.load()
            if img.mode in ("RGBA", "LA", "P") or ("transparency" in img.info):
                # composite on white so transparent areas are paper
                rgba = img.convert("RGBA")
                bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                bg.alpha_composite(rgba)
                img = bg
            gray = img.convert("L")
            return cls(gray.width, gray.height, bytearray(gray.tobytes()))
        except ImportError:
            pass
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return _decode_png_gray(data)
        raise ValueError("Cannot decode this image: Pillow is not available and the "
                         "data is not a PNG. Convert the image to PNG (or install Pillow).")

    @classmethod
    def from_file(cls, path):
        with open(path, "rb") as fh:
            return cls.from_bytes(fh.read())

    @classmethod
    def from_data_uri(cls, uri):
        """Decode ``data:image/png;base64,....``."""
        head, _, payload = uri.partition(",")
        if ";base64" in head:
            data = base64.b64decode(payload)
        else:
            from urllib.parse import unquote_to_bytes
            data = unquote_to_bytes(payload)
        return cls.from_bytes(data)

    # -- sampling -----------------------------------------------------------
    def darkness_grid(self, cols, rows):
        """Box-average the image into *rows* x *cols* cells of darkness 0..1
        (1 = black).  Returns a list of rows (lists of floats)."""
        cols = max(1, int(cols))
        rows = max(1, int(rows))
        try:
            from PIL import Image  # noqa: WPS433
            img = Image.frombytes("L", (self.width, self.height), bytes(self.pixels))
            small = img.resize((cols, rows), Image.BOX)
            raw = small.tobytes()
            return [[1.0 - raw[r * cols + c] / 255.0 for c in range(cols)] for r in range(rows)]
        except ImportError:
            pass
        return _box_downsample(self.pixels, self.width, self.height, cols, rows)


def _box_downsample(px, w, h, cols, rows):
    """Pure-Python area averaging (used when Pillow is missing)."""
    out = []
    for r in range(rows):
        y0 = r * h // rows
        y1 = max(y0 + 1, (r + 1) * h // rows)
        row = []
        for c in range(cols):
            x0 = c * w // cols
            x1 = max(x0 + 1, (c + 1) * w // cols)
            total = 0
            for y in range(y0, y1):
                base = y * w
                total += sum(px[base + x0:base + x1])
            n = (y1 - y0) * (x1 - x0)
            row.append(1.0 - total / (255.0 * n))
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Minimal PNG decoder (8-bit, non-interlaced; gray / gray+alpha / RGB / RGBA / palette)
# ---------------------------------------------------------------------------

def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _decode_png_gray(data):
    pos = 8
    width = height = None
    bit_depth = color_type = interlace = None
    palette = None
    idat = []
    while pos < len(data):
        length, = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif ctype == b"PLTE":
            palette = body
        elif ctype == b"IDAT":
            idat.append(body)
        elif ctype == b"IEND":
            break
    if width is None:
        raise ValueError("invalid PNG (no IHDR)")
    if bit_depth != 8 or interlace != 0:
        raise ValueError("only 8-bit non-interlaced PNGs are supported without Pillow "
                         "(re-save the image as 8-bit PNG)")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError("unsupported PNG colour type %d" % color_type)
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    out = bytearray(width * height)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        ftype = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ftype == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ftype == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                c = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 255
        # to gray (composite alpha on white)
        base = y * width
        if color_type == 0:
            out[base:base + width] = line
        elif color_type == 2:
            for x in range(width):
                r, g, b = line[3 * x], line[3 * x + 1], line[3 * x + 2]
                out[base + x] = (r * 299 + g * 587 + b * 114) // 1000
        elif color_type == 3:
            for x in range(width):
                i = line[x] * 3
                r, g, b = palette[i], palette[i + 1], palette[i + 2]
                out[base + x] = (r * 299 + g * 587 + b * 114) // 1000
        elif color_type == 4:
            for x in range(width):
                g, a = line[2 * x], line[2 * x + 1]
                out[base + x] = (g * a + 255 * (255 - a)) // 255
        else:  # RGBA
            for x in range(width):
                r, g, b, a = line[4 * x], line[4 * x + 1], line[4 * x + 2], line[4 * x + 3]
                g = (r * 299 + g * 587 + b * 114) // 1000
                out[base + x] = (g * a + 255 * (255 - a)) // 255
        prev = line
    return GrayImage(width, height, out)


# ---------------------------------------------------------------------------
# The squiggle itself
# ---------------------------------------------------------------------------

class SquiggleParams:
    """All lengths are in the output coordinate units (the caller's)."""

    def __init__(self, rows=80, amp_min=0.0, amp_max=0.9, freq_min=0.3, freq_max=2.5,
                 gamma=1.0, invert=False, white_cutoff=0.04, tolerance=0.1,
                 vertical=False, samples_per_pitch=4):
        self.rows = max(2, int(rows))
        self.amp_min = max(0.0, float(amp_min))      # fraction of row pitch (peak-to-peak)
        self.amp_max = max(self.amp_min, float(amp_max))
        self.freq_min = max(0.01, float(freq_min))   # wavelengths per row pitch
        self.freq_max = max(self.freq_min, float(freq_max))
        self.gamma = max(0.1, float(gamma))          # tone curve exponent (>1 lightens mids)
        self.invert = bool(invert)
        self.white_cutoff = max(0.0, float(white_cutoff))  # darkness below -> straight line
        self.tolerance = max(1e-6, float(tolerance))  # chord error for sampling the sine
        self.vertical = bool(vertical)
        self.samples_per_pitch = max(1, int(samples_per_pitch))


def squiggle(image, width, height, params):
    """Render *image* (GrayImage) into a box of *width* x *height* units.

    Returns (points, info).  *points* is a single polyline starting at the
    top-left (0, 0) corner region; *info* has row pitch, point count and the
    path length.
    """
    p = params
    if p.vertical:
        # Render transposed, then swap coordinates back.
        pts, info = squiggle(_transpose(image), height, width,
                             _copy_params(p, vertical=False))
        pts = [(y, x) for x, y in pts]
        return pts, info

    rows = p.rows
    pitch = height / rows
    cols = max(2, int(math.ceil(width / pitch * p.samples_per_pitch)))
    grid = image.darkness_grid(cols, rows)
    cell_w = width / cols

    tol = p.tolerance
    two_pi = 2.0 * math.pi
    out = []
    px = py = None

    def emit(x, y):
        nonlocal px, py
        out.append((x, y))
        px, py = x, y

    for r in range(rows):
        row = grid[r]
        yc = (r + 0.5) * pitch
        forward = (r % 2 == 0)

        def darkness_at(x):
            # linear interpolation between cell centres
            fx = x / cell_w - 0.5
            i0 = int(math.floor(fx))
            t = fx - i0
            i0 = min(max(i0, 0), cols - 1)
            i1 = min(max(i0 + 1, 0), cols - 1)
            d = row[i0] * (1.0 - t) + row[i1] * t
            if p.invert:
                d = 1.0 - d
            d = min(1.0, max(0.0, d))
            if d < p.white_cutoff:
                return 0.0
            return d ** p.gamma

        x = 0.0 if forward else width
        emit(x, yc)
        phase = 0.0
        straight_run = False
        while True:
            d = darkness_at(x)
            amp = (p.amp_min + (p.amp_max - p.amp_min) * d) * pitch * 0.5   # half amplitude
            freq = p.freq_min + (p.freq_max - p.freq_min) * d
            wavelength = pitch / freq
            if amp < tol * 0.5:
                step = pitch / p.samples_per_pitch
                straight = True
            else:
                # chord error of a sine: n points/wavelength = 2*pi*sqrt(A/(8 tol))
                n = int(math.ceil(two_pi * math.sqrt(amp / (8.0 * tol))))
                n = min(64, max(8, n))
                step = wavelength / n
                straight = False
            remaining = (width - x) if forward else x
            if remaining <= 1e-9:
                break
            if step > remaining:
                step = remaining
            x = x + step if forward else x - step
            phase += two_pi * freq * step / pitch
            if straight:
                # collapse consecutive straight samples into one segment
                if straight_run:
                    out[-1] = (x, yc)
                    px, py = x, yc
                else:
                    emit(x, yc)
                straight_run = True
            else:
                emit(x, yc + amp * math.sin(phase))
                straight_run = False
        # make sure the row ends exactly at the edge, on the centre line
        if abs(py - yc) > 1e-9 or abs(px - (width if forward else 0.0)) > 1e-9:
            emit(width if forward else 0.0, yc)

    length = 0.0
    for (ax, ay), (bx, by) in zip(out, out[1:]):
        length += math.hypot(bx - ax, by - ay)
    info = {
        "rows": rows,
        "pitch": pitch,
        "points": len(out),
        "length": length,
        "width": width,
        "height": height,
    }
    return out, info


def _transpose(image):
    w, h = image.width, image.height
    src = image.pixels
    dst = bytearray(w * h)
    for y in range(h):
        base = y * w
        for x in range(w):
            dst[x * h + y] = src[base + x]
    return GrayImage(h, w, dst)


def _copy_params(p, **over):
    q = SquiggleParams(rows=p.rows, amp_min=p.amp_min, amp_max=p.amp_max, freq_min=p.freq_min,
                       freq_max=p.freq_max, gamma=p.gamma, invert=p.invert,
                       white_cutoff=p.white_cutoff, tolerance=p.tolerance, vertical=p.vertical,
                       samples_per_pitch=p.samples_per_pitch)
    for k, v in over.items():
        setattr(q, k, v)
    return q


def format_squiggle_report(info, units_per_mm=1.0, feed_mm_min=2000.0):
    L = info["length"] / units_per_mm
    minutes = L / feed_mm_min if feed_mm_min > 0 else 0.0
    return "\n".join([
        "Squiggle Raster report",
        "  image box:        %.1f x %.1f mm" % (info["width"] / units_per_mm, info["height"] / units_per_mm),
        "  rows:             %d  (pitch %.2f mm)" % (info["rows"], info["pitch"] / units_per_mm),
        "  points:           %d" % info["points"],
        "  path length:      %.0f mm  (%.1f m)" % (L, L / 1000.0),
        "  nominal time:     %.0f min at F%d (real time is longer: acceleration "
        "limits on the wiggles)" % (minutes, feed_mm_min),
    ])
