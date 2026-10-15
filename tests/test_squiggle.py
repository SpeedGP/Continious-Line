"""Tests for cl_squiggle (pure Python; no Pillow or Inkscape needed)."""
import math
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cl_geometry import points_to_d  # noqa: E402
from cl_squiggle import (  # noqa: E402
    GrayImage, SquiggleParams, _box_downsample, _decode_png_gray, format_squiggle_report,
    squiggle,
)


def make_png(width, height, rows_rgb, color_type=2, filt=0):
    """Encode a tiny PNG with one filter type for the decoder test."""
    ch = {0: 1, 2: 3, 6: 4}[color_type]
    raw = bytearray()
    prev = bytearray(width * ch)
    for row in rows_rgb:
        line = bytearray(row)
        enc = bytearray(line)
        if filt == 1:
            for i in range(width * ch - 1, ch - 1, -1):
                enc[i] = (line[i] - line[i - ch]) & 255
        elif filt == 2:
            for i in range(width * ch):
                enc[i] = (line[i] - prev[i]) & 255
        raw.append(filt)
        raw.extend(enc)
        prev = line

    def chunk(t, b):
        c = struct.pack(">I", len(b)) + t + b
        return c + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw)))
            + chunk(b"IEND", b""))


def gradient_image(w=64, h=16):
    """Left white -> right black."""
    px = bytearray()
    for _ in range(h):
        for x in range(w):
            px.append(255 - int(255 * x / (w - 1)))
    return GrayImage(w, h, px)


def test_png_decoder_filters():
    rows = [[(x * 7 + y * 13) & 255 for x in range(5) for _ in range(3)] for y in range(4)]
    for filt in (0, 1, 2):
        img = _decode_png_gray(make_png(5, 4, rows, color_type=2, filt=filt))
        assert img.width == 5 and img.height == 4
        # all three channels equal -> gray == channel value
        assert img.pixels[0] == rows[0][0]
        assert img.pixels[5 * 3 + 2] == rows[3][6]
    # grayscale + alpha composites on white
    rows = [[0, 0] for _ in range(3)]          # black, fully transparent
    img = _decode_png_gray(make_png(3, 1, [sum(rows, [])], color_type=6) if False else
                           make_png(1, 1, [[0, 0, 0, 0]], color_type=6))
    assert img.pixels[0] == 255


def test_from_bytes_png_roundtrip():
    rows = [[v for v in (10, 200, 30)] for _ in range(1)]
    data = make_png(1, 1, rows, color_type=2)
    img = GrayImage.from_bytes(data)
    assert img.width == 1 and 100 < img.pixels[0] < 160   # luma of (10,200,30)
    assert img.pixels[0] == GrayImage.from_data_uri(
        "data:image/png;base64," + __import__("base64").b64encode(data).decode()).pixels[0]


def test_box_downsample():
    img = gradient_image(64, 16)
    grid = _box_downsample(img.pixels, 64, 16, 4, 2)
    assert len(grid) == 2 and len(grid[0]) == 4
    assert grid[0][0] < grid[0][1] < grid[0][2] < grid[0][3]
    assert abs(grid[0][0] - grid[1][0]) < 1e-9          # rows identical


def test_squiggle_single_polyline_and_rows():
    img = gradient_image(64, 16)
    p = SquiggleParams(rows=4, tolerance=0.05)
    pts, info = squiggle(img, 100.0, 25.0, p)
    assert info["rows"] == 4 and abs(info["pitch"] - 6.25) < 1e-9
    assert len(pts) == info["points"] and len(pts) > 20
    d = points_to_d(pts)
    assert d.count("M") == 1 and "Z" not in d
    # starts at left edge on the first row centre line, ends at an edge
    assert pts[0] == (0.0, 3.125)
    assert pts[-1][0] in (0.0, 100.0)
    # boustrophedon: row 0 ends at the right edge, row 1 at the left
    xs_end = [q[0] for q in pts if abs(q[1] - 3.125) < 1e-9]
    assert xs_end[-1] == 100.0
    # consecutive points are connected (no jumps bigger than a pitch and a bit)
    for a, b in zip(pts, pts[1:]):
        assert math.hypot(b[0] - a[0], b[1] - a[1]) <= 6.25 * 1.01 + 1e-9
    # length consistent
    L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    assert abs(L - info["length"]) < 1e-6


def test_amplitude_follows_darkness():
    img = gradient_image(200, 10)
    p = SquiggleParams(rows=2, amp_max=0.9, freq_max=3.0, tolerance=0.02, white_cutoff=0.0)
    pts, info = squiggle(img, 200.0, 20.0, p)
    row0 = [q for q in pts if q[1] < 10.0 + 1e-9][: len(pts) // 2]
    left = [abs(q[1] - 5.0) for q in row0 if q[0] < 40]
    right = [abs(q[1] - 5.0) for q in row0 if q[0] > 160]
    assert max(left) < max(right)
    assert max(right) > 0.35 * 10.0        # near-black -> close to 0.45 * pitch
    # points per unit length is higher where it is darker (higher frequency)
    assert len(right) > len(left)


def test_white_is_straight_and_compact():
    img = GrayImage(32, 8, bytearray([255] * 256))
    pts, info = squiggle(img, 50.0, 10.0, SquiggleParams(rows=4))
    assert all(abs(q[1] - ((r + 0.5) * 2.5)) < 1e-9 for q in pts
               for r in [min(range(4), key=lambda r: abs(q[1] - (r + 0.5) * 2.5))])
    assert info["points"] <= 4 * 3            # ~2-3 points per straight row
    assert abs(info["length"] - (4 * 50.0 + 3 * 2.5)) < 1e-6


def test_invert_and_vertical():
    img = gradient_image(64, 16)
    p = SquiggleParams(rows=4, invert=True, white_cutoff=0.0)
    pts, _ = squiggle(img, 100.0, 25.0, p)
    # inverted: the (originally white) left side now wiggles, the right is flat
    row0 = [q for q in pts[: len(pts) // 4]]
    assert max(abs(q[1] - 3.125) for q in row0 if q[0] < 20) > \
        max([abs(q[1] - 3.125) for q in row0 if q[0] > 95] or [0])
    pv, infov = squiggle(img, 100.0, 25.0, SquiggleParams(rows=5, vertical=True))
    assert infov["rows"] == 5
    assert pv[0][0] == 10.0 and pv[0][1] == 0.0  # first column centre, top edge
    assert points_to_d(pv).count("M") == 1


def test_report():
    img = gradient_image(32, 8)
    _, info = squiggle(img, 100.0, 25.0, SquiggleParams(rows=4))
    txt = format_squiggle_report(info, feed_mm_min=2000)
    assert "rows:             4" in txt and "path length" in txt


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # pylint: disable=broad-except
                failures += 1
                print("FAIL", name, "--", repr(exc))
    sys.exit(1 if failures else 0)
