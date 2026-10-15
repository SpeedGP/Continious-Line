"""
cl_geometry.py -- pure-Python geometry helpers for the Continuous Line extension.

Standard library only.  Nothing in here imports inkex, so it can be used by
the Inkscape extension, the CLI, the tests and any future G-code
post-processor alike.

Contents
--------
* Bezier flattening (cubic / quadratic / elliptical arc -> polyline) with a
  user-settable chordal tolerance.
* 2-D affine transforms in SVG matrix form (a, b, c, d, e, f), including an
  SVG ``transform="..."`` attribute parser.
* A small SVG path-data (``d`` attribute) parser that produces polylines.
* Bounding-box helpers and document-unit conversion (mm -> user units).
"""

import math
import re

# ---------------------------------------------------------------------------
# Transforms  (SVG convention:  x' = a*x + c*y + e ;  y' = b*x + d*y + f)
# ---------------------------------------------------------------------------

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mat_apply(m, pt):
    """Apply matrix *m* to point *pt*."""
    a, b, c, d, e, f = m
    x, y = pt
    return (a * x + c * y + e, b * x + d * y + f)


def mat_mul(m1, m2):
    """Return m1 * m2 (apply m2 first, then m1) -- same order as SVG nesting."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def mat_scale_factor(m):
    """Approximate uniform scale of a matrix (sqrt of |det|).

    Used to convert a tolerance expressed in document user units into the
    local coordinate system of a transformed element, or vice versa.
    """
    a, b, c, d, _, _ = m
    det = abs(a * d - b * c)
    return math.sqrt(det) if det > 0 else 1.0


_TRANSFORM_RE = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")
_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_transform(text):
    """Parse an SVG ``transform`` attribute into a matrix tuple."""
    m = IDENTITY
    if not text:
        return m
    for name, args in _TRANSFORM_RE.findall(text):
        nums = [float(v) for v in _NUM_RE.findall(args)]
        name = name.strip().lower()
        if name == "matrix" and len(nums) == 6:
            t = tuple(nums)
        elif name == "translate":
            tx = nums[0] if nums else 0.0
            ty = nums[1] if len(nums) > 1 else 0.0
            t = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            t = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate":
            ang = math.radians(nums[0]) if nums else 0.0
            ca, sa = math.cos(ang), math.sin(ang)
            t = (ca, sa, -sa, ca, 0.0, 0.0)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                t = mat_mul(mat_mul((1, 0, 0, 1, cx, cy), t), (1, 0, 0, 1, -cx, -cy))
        elif name == "skewx":
            t = (1.0, 0.0, math.tan(math.radians(nums[0])), 1.0, 0.0, 0.0)
        elif name == "skewy":
            t = (1.0, math.tan(math.radians(nums[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        m = mat_mul(m, t)
    return m


# ---------------------------------------------------------------------------
# Bezier flattening
# ---------------------------------------------------------------------------

_MAX_SEGMENTS = 2000


def _wang_segments(dd, degree, tol):
    """Number of uniform parameter steps so that the chordal error <= tol.

    Wang's formula: n = ceil( sqrt( k(k-1)/8 * dd / tol ) ) where dd is the
    largest second difference of the control polygon and k the degree.
    """
    if dd <= 0.0 or tol <= 0.0:
        return 1
    n = math.ceil(math.sqrt(degree * (degree - 1) / 8.0 * dd / tol))
    return max(1, min(_MAX_SEGMENTS, int(n)))


def flatten_cubic(p0, p1, p2, p3, tol):
    """Flatten a cubic Bezier.  Returns the list of points AFTER p0 (ends with p3)."""
    dd = max(
        math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]),
        math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]),
    )
    n = _wang_segments(dd, 3, tol)
    if n == 1:
        return [p3]
    out = []
    for i in range(1, n):
        t = i / n
        mt = 1.0 - t
        a = mt * mt * mt
        b = 3.0 * mt * mt * t
        c = 3.0 * mt * t * t
        d = t * t * t
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    out.append(p3)
    return out


def flatten_quadratic(p0, p1, p2, tol):
    """Flatten a quadratic Bezier.  Returns the list of points AFTER p0."""
    dd = math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1])
    n = _wang_segments(dd, 2, tol)
    if n == 1:
        return [p2]
    out = []
    for i in range(1, n):
        t = i / n
        mt = 1.0 - t
        a = mt * mt
        b = 2.0 * mt * t
        c = t * t
        out.append((a * p0[0] + b * p1[0] + c * p2[0],
                    a * p0[1] + b * p1[1] + c * p2[1]))
    out.append(p2)
    return out


def flatten_arc(p0, rx, ry, x_axis_rotation, large_arc, sweep, p1, tol):
    """Flatten an SVG elliptical arc (endpoint parameterisation).

    Returns the list of points AFTER p0 (ends with p1).  Follows the SVG 1.1
    implementation notes (F.6.5) for the centre conversion.
    """
    x1, y1 = p0
    x2, y2 = p1
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [p1]
    phi = math.radians(x_axis_rotation % 360.0)
    cp, sp = math.cos(phi), math.sin(phi)
    dx2, dy2 = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cp * dx2 + sp * dy2
    y1p = -sp * dx2 + cp * dy2
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large_arc == sweep:
        coef = -coef
    cxp = coef * (rx * y1p / ry)
    cyp = coef * (-ry * x1p / rx)
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2.0
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2.0

    def ang(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        ln = math.hypot(ux, uy) * math.hypot(vx, vy)
        a = math.acos(max(-1.0, min(1.0, dot / ln)))
        if ux * vy - uy * vx < 0:
            a = -a
        return a

    theta1 = ang(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dtheta > 0:
        dtheta -= 2 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2 * math.pi
    # chord error for radius r and step angle a is r(1-cos(a/2)) <= tol
    r = max(rx, ry)
    if tol <= 0 or tol >= r:
        n = 1
    else:
        step = 2.0 * math.acos(1.0 - tol / r)
        n = max(1, min(_MAX_SEGMENTS, int(math.ceil(abs(dtheta) / step))))
    out = []
    for i in range(1, n):
        t = theta1 + dtheta * i / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append((cp * ex - sp * ey + cx, sp * ex + cp * ey + cy))
    out.append(p1)
    return out


def ellipse_polyline(cx, cy, rx, ry, tol):
    """Closed polyline approximating a full ellipse (first point repeated last)."""
    r = max(abs(rx), abs(ry))
    if r <= 0:
        return []
    if tol <= 0 or tol >= r:
        n = 8
    else:
        step = 2.0 * math.acos(1.0 - tol / r)
        n = max(8, min(_MAX_SEGMENTS, int(math.ceil(2 * math.pi / step))))
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        pts.append((cx + rx * math.cos(t), cy + ry * math.sin(t)))
    pts.append(pts[0])
    return pts


# ---------------------------------------------------------------------------
# SVG path-data parser  (M L H V C S Q T A Z, absolute and relative)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")

_ARGC = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def parse_path_d(d, tol, transform=None):
    """Convert an SVG ``d`` string into a list of polylines.

    Each polyline is a list of (x, y) tuples.  Closed subpaths are returned
    with their first point repeated at the end.  Curves are flattened with
    chordal tolerance *tol* (in the coordinate system of the path; callers
    should pre-divide a document tolerance by the transform scale).  If
    *transform* is given, every output point is transformed with it.
    """
    tokens = _TOKEN_RE.findall(d or "")
    polylines = []
    cur = []
    cx = cy = 0.0          # current point
    sx = sy = 0.0          # subpath start
    last_ctrl = None       # last control point for S / T
    last_cmd = None
    i = 0
    n = len(tokens)

    def flush(closed):
        nonlocal cur
        if cur:
            pts = cur
            if closed and len(pts) > 1 and pts[-1] != pts[0]:
                pts.append(pts[0])
            if len(pts) >= 1:
                polylines.append(pts)
        cur = []

    cmd = None
    while i < n:
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                flush(True)
                cx, cy = sx, sy
                last_ctrl = None
                last_cmd = "Z"
                continue
        elif cmd is None:
            i += 1
            continue
        up = cmd.upper()
        rel = cmd.islower()
        argc = _ARGC[up]
        if i + argc > n:
            break
        try:
            args = [float(t) for t in tokens[i:i + argc]]
        except ValueError:
            break
        i += argc
        ox, oy = (cx, cy) if rel else (0.0, 0.0)

        if up == "M":
            flush(False)
            cx, cy = args[0] + ox, args[1] + oy
            sx, sy = cx, cy
            cur = [(cx, cy)]
            last_ctrl = None
            # subsequent pairs are implicit linetos
            cmd = "l" if rel else "L"
        elif up == "L":
            cx, cy = args[0] + ox, args[1] + oy
            cur.append((cx, cy))
            last_ctrl = None
        elif up == "H":
            cx = args[0] + ox
            cur.append((cx, cy))
            last_ctrl = None
        elif up == "V":
            cy = args[0] + oy
            cur.append((cx, cy))
            last_ctrl = None
        elif up in ("C", "S"):
            if up == "C":
                p1 = (args[0] + ox, args[1] + oy)
                p2 = (args[2] + ox, args[3] + oy)
                p3 = (args[4] + ox, args[5] + oy)
            else:
                if last_cmd in ("C", "S") and last_ctrl is not None:
                    p1 = (2 * cx - last_ctrl[0], 2 * cy - last_ctrl[1])
                else:
                    p1 = (cx, cy)
                p2 = (args[0] + ox, args[1] + oy)
                p3 = (args[2] + ox, args[3] + oy)
            if not cur:
                cur = [(cx, cy)]
            cur.extend(flatten_cubic((cx, cy), p1, p2, p3, tol))
            last_ctrl = p2
            cx, cy = p3
        elif up in ("Q", "T"):
            if up == "Q":
                p1 = (args[0] + ox, args[1] + oy)
                p2 = (args[2] + ox, args[3] + oy)
            else:
                if last_cmd in ("Q", "T") and last_ctrl is not None:
                    p1 = (2 * cx - last_ctrl[0], 2 * cy - last_ctrl[1])
                else:
                    p1 = (cx, cy)
                p2 = (args[0] + ox, args[1] + oy)
            if not cur:
                cur = [(cx, cy)]
            cur.extend(flatten_quadratic((cx, cy), p1, p2, tol))
            last_ctrl = p1
            cx, cy = p2
        elif up == "A":
            p1 = (args[5] + ox, args[6] + oy)
            if not cur:
                cur = [(cx, cy)]
            cur.extend(flatten_arc((cx, cy), args[0], args[1], args[2],
                                   bool(args[3]), bool(args[4]), p1, tol))
            last_ctrl = None
            cx, cy = p1
        last_cmd = up
    flush(False)

    if transform is not None and transform != IDENTITY:
        polylines = [[mat_apply(transform, p) for p in pl] for pl in polylines]
    return polylines


# ---------------------------------------------------------------------------
# Bounding boxes
# ---------------------------------------------------------------------------

def bbox_of_points(points):
    """(xmin, ymin, xmax, ymax) of an iterable of points, or None if empty."""
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    found = False
    for x, y in points:
        found = True
        if x < xmin:
            xmin = x
        if x > xmax:
            xmax = x
        if y < ymin:
            ymin = y
        if y > ymax:
            ymax = y
    return (xmin, ymin, xmax, ymax) if found else None


def bbox_of_polylines(polylines):
    boxes = [b for b in (bbox_of_points(pl) for pl in polylines) if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


CORNERS = ("bottom-left", "top-left", "top-right", "bottom-right")


def corner_point(bbox, corner):
    """Return the (x, y) of the named corner of *bbox*.

    SVG's y axis points DOWN, so "bottom" is the maximum y.  This matches
    what you see on the page in Inkscape; J Tech flips Y for the machine.
    """
    xmin, ymin, xmax, ymax = bbox
    corner = (corner or "bottom-left").lower().replace("_", "-")
    x = xmin if "left" in corner else xmax
    y = ymax if "bottom" in corner else ymin
    return (x, y)


def points_to_d(points, precision=3):
    """SVG path data with exactly ONE subpath: absolute M, then L pairs, no Z."""
    if not points:
        return ""
    fmt = "%%.%df" % precision

    def num(v):
        s = fmt % v
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s

    parts = ["M", num(points[0][0]), num(points[0][1]), "L"]
    for x, y in points[1:]:
        parts.append(num(x))
        parts.append(num(y))
    return " ".join(parts)


def polyline_length(points):
    total = 0.0
    for i in range(1, len(points)):
        total += math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
    return total


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

# CSS absolute units expressed in px (96 px per inch).
_UNIT_TO_PX = {
    "px": 1.0, "pt": 96.0 / 72.0, "pc": 16.0, "mm": 96.0 / 25.4,
    "cm": 96.0 / 2.54, "in": 96.0, "q": 96.0 / 101.6, "": 1.0,
}


def parse_length(text):
    """Split a CSS length like '210mm' into (value, unit)."""
    m = re.match(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)", text or "")
    if not m:
        return None, ""
    return float(m.group(1)), m.group(2).lower()


def user_units_per_mm(width_attr, viewbox_attr):
    """How many SVG user units make one millimetre on the page.

    Inkscape documents commonly have width="210mm" with viewBox="0 0 210 297"
    (1 uu = 1 mm) but also width="8.5in" viewBox="0 0 816 1056" (1 uu = 1 px)
    and everything in between.  The scale is width / viewBox-width.
    """
    vb = [float(v) for v in _NUM_RE.findall(viewbox_attr or "")]
    val, unit = parse_length(width_attr)
    if val is None or val == 0:
        # No usable width: assume user units are px.
        return _UNIT_TO_PX["mm"]
    if unit == "%":
        return _UNIT_TO_PX["mm"]
    width_px = val * _UNIT_TO_PX.get(unit, 1.0)
    width_mm = width_px / _UNIT_TO_PX["mm"]
    if len(vb) == 4 and vb[2] > 0:
        return vb[2] / width_mm
    # No viewBox: user unit == px
    return _UNIT_TO_PX["mm"]
