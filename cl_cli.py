#!/usr/bin/env python3
"""
cl_cli.py -- run the Continuous Line core on an SVG file without Inkscape.

    python cl_cli.py input.svg output.svg [options]

This is a *testing* tool: it uses a minimal SVG reader built on the standard
library (xml.etree).  It understands <path> (M L H V C S Q T A Z, absolute
and relative), <rect> (incl. rounded corners), <circle>, <ellipse>, <line>,
<polyline>, <polygon> and nested <g> transforms.  <use>, <text> and <image>
are skipped with a warning.  CSS classes / external stylesheets are ignored
(geometry only, which is all the algorithm needs).

The Inkscape extension (continuous_line.py) is the real entry point; it uses
inkex for the SVG side and the very same cl_pipeline core.

Optional: ``--png-before X.png --png-after Y.png`` render simple previews
with Pillow if it happens to be installed (not required).
"""

import argparse
import math
import sys
import xml.etree.ElementTree as ET

from cl_geometry import (
    IDENTITY, bbox_of_polylines, ellipse_polyline, flatten_arc, mat_apply, mat_mul,
    mat_scale_factor, parse_path_d, parse_transform, user_units_per_mm,
)
from cl_pipeline import format_report, run_pipeline

SVG_NS = "http://www.w3.org/2000/svg"


def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _f(el, name, default=0.0):
    v = el.get(name)
    if v is None:
        return default
    try:
        return float(v.rstrip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ%"))
    except ValueError:
        return default


def _rect_polyline(el, tol):
    x, y, w, h = _f(el, "x"), _f(el, "y"), _f(el, "width"), _f(el, "height")
    rx, ry = el.get("rx"), el.get("ry")
    rx = float(rx) if rx is not None else (float(ry) if ry is not None else 0.0)
    ry = float(ry) if ry is not None else rx
    rx = min(abs(rx), w / 2.0)
    ry = min(abs(ry), h / 2.0)
    if w <= 0 or h <= 0:
        return []
    if rx <= 0 or ry <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
    pts = [(x + rx, y)]
    pts.append((x + w - rx, y))
    pts.extend(flatten_arc(pts[-1], rx, ry, 0, False, True, (x + w, y + ry), tol))
    pts.append((x + w, y + h - ry))
    pts.extend(flatten_arc(pts[-1], rx, ry, 0, False, True, (x + w - rx, y + h), tol))
    pts.append((x + rx, y + h))
    pts.extend(flatten_arc(pts[-1], rx, ry, 0, False, True, (x, y + h - ry), tol))
    pts.append((x, y + ry))
    pts.extend(flatten_arc(pts[-1], rx, ry, 0, False, True, (x + rx, y), tol))
    return pts


def _points_attr(el):
    import re
    nums = [float(v) for v in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", el.get("points", ""))]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def _is_hidden(el):
    style = el.get("style", "") or ""
    if "display:none" in style.replace(" ", ""):
        return True
    return el.get("display") == "none" or el.get("visibility") == "hidden"


def collect_polylines(root, flatten_tol):
    """Walk the SVG tree and return (polylines, warnings) in root user units."""
    polylines = []
    warnings = []

    def walk(el, parent_tr):
        tag = _local(el.tag)
        if tag in ("defs", "metadata", "namedview", "title", "desc", "style",
                   "clipPath", "mask", "marker", "pattern", "symbol", "linearGradient",
                   "radialGradient", "filter", "sodipodi:namedview"):
            return
        if _is_hidden(el):
            return
        tr = mat_mul(parent_tr, parse_transform(el.get("transform")))
        if tag in ("svg", "g", "a", "switch"):
            for child in el:
                walk(child, tr)
            return
        # tolerance in local units so that the flattened result honours the
        # document tolerance after transformation
        local_tol = flatten_tol / mat_scale_factor(tr)
        pls = None
        if tag == "path":
            pls = parse_path_d(el.get("d", ""), local_tol)
        elif tag == "rect":
            pls = [_rect_polyline(el, local_tol)]
        elif tag == "circle":
            r = _f(el, "r")
            pls = [ellipse_polyline(_f(el, "cx"), _f(el, "cy"), r, r, local_tol)]
        elif tag == "ellipse":
            pls = [ellipse_polyline(_f(el, "cx"), _f(el, "cy"), _f(el, "rx"), _f(el, "ry"), local_tol)]
        elif tag == "line":
            pls = [[(_f(el, "x1"), _f(el, "y1")), (_f(el, "x2"), _f(el, "y2"))]]
        elif tag == "polyline":
            pls = [_points_attr(el)]
        elif tag == "polygon":
            pts = _points_attr(el)
            if pts:
                pts = pts + [pts[0]]
            pls = [pts]
        elif tag == "use":
            warnings.append("<use id=%r> clone skipped (unlink clones first)" % el.get("id"))
            return
        elif tag in ("text", "tspan", "flowRoot", "textPath"):
            warnings.append("<%s id=%r> skipped: convert text to path first (Path > Object to Path)"
                            % (tag, el.get("id")))
            return
        elif tag == "image":
            warnings.append("<image id=%r> skipped (trace it to paths first)" % el.get("id"))
            return
        else:
            return
        for pl in pls:
            if len(pl) >= 2:
                polylines.append([mat_apply(tr, p) for p in pl] if tr != IDENTITY else pl)

    walk(root, IDENTITY)
    return polylines, warnings


from cl_geometry import points_to_d  # noqa: E402  (re-exported for tests)


def write_svg(src_root, points, out_path, stroke_width_uu):
    """Write an SVG with the same canvas as *src_root* containing one path."""
    attrs = {"xmlns": SVG_NS}
    for key in ("width", "height", "viewBox"):
        if src_root.get(key) is not None:
            attrs[key] = src_root.get(key)
    svg = ET.Element("svg", attrs)
    path = ET.SubElement(svg, "path")
    path.set("id", "continuous-line")
    path.set("d", points_to_d(points))
    path.set("style", "fill:none;stroke:#000000;stroke-width:%.4g;stroke-linecap:round;"
                      "stroke-linejoin:round" % stroke_width_uu)
    ET.ElementTree(svg).write(out_path, encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------------------
# optional preview rendering (Pillow)
# ---------------------------------------------------------------------------

def render_png(polylines, out_path, width_px=1000, highlight=None, bbox=None):
    """Draw polylines to a PNG with Pillow.  *highlight* is a list of
    (polyline, colour) drawn on top (used for bridge / retrace previews)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow not installed; cannot render %s" % out_path, file=sys.stderr)
        return False
    bb = bbox or bbox_of_polylines(polylines + [pl for pl, _ in (highlight or [])])
    if not bb:
        return False
    xmin, ymin, xmax, ymax = bb
    w = max(xmax - xmin, 1e-9)
    h = max(ymax - ymin, 1e-9)
    margin = 20
    scale = (width_px - 2 * margin) / w
    height_px = int(h * scale) + 2 * margin
    img = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(img)

    def tx(p):
        return (margin + (p[0] - xmin) * scale, margin + (p[1] - ymin) * scale)

    for pl in polylines:
        if len(pl) >= 2:
            draw.line([tx(p) for p in pl], fill=(0, 0, 0), width=1)
    for pl, colour in highlight or []:
        if len(pl) >= 2:
            draw.line([tx(p) for p in pl], fill=colour, width=2)
    img.save(out_path)
    return True


def highlight_segments(result):
    """Return [(segment, colour)] for bridges (red) and retraces (blue)."""
    from cl_graph import KIND_BRIDGE, KIND_RETRACE
    g = result.graph
    out = []
    for eid in range(g.num_edges()):
        k = g.ekind[eid]
        if k == KIND_BRIDGE:
            out.append(([g.nodes[g.eu[eid]], g.nodes[g.ev[eid]]], (220, 0, 0)))
        elif k == KIND_RETRACE:
            out.append(([g.nodes[g.eu[eid]], g.nodes[g.ev[eid]]], (0, 90, 220)))
    return out


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Merge all paths of an SVG into one continuous line.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--flatten-tol", type=float, default=0.1, help="curve flatten tolerance, mm (default 0.1)")
    ap.add_argument("--snap-tol", type=float, default=0.1, help="merge/snap tolerance, mm (default 0.1)")
    ap.add_argument("--start-corner", default="bottom-left",
                    choices=["bottom-left", "top-left", "top-right", "bottom-right"])
    ap.add_argument("--min-island", type=float, default=0.0, help="drop closed contours smaller than this (mm)")
    ap.add_argument("--open-path", action="store_true", help="allow the line to end away from the start")
    ap.add_argument("--no-dedupe", action="store_true", help="keep exactly duplicated segments")
    ap.add_argument("--stroke-width", type=float, default=0.3, help="stroke width of the output path, mm")
    ap.add_argument("--png-before", help="render the input geometry to this PNG (needs Pillow)")
    ap.add_argument("--png-after", help="render the result to this PNG (needs Pillow)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    tree = ET.parse(args.input)
    root = tree.getroot()
    uu_per_mm = user_units_per_mm(root.get("width"), root.get("viewBox"))

    polylines, warnings = collect_polylines(root, args.flatten_tol * uu_per_mm)
    for wmsg in warnings:
        print("warning: " + wmsg, file=sys.stderr)
    if not polylines:
        print("No drawable geometry found.", file=sys.stderr)
        return 2

    result = run_pipeline(
        polylines,
        snap_tol=args.snap_tol * uu_per_mm,
        start_corner=args.start_corner,
        min_island=args.min_island * uu_per_mm,
        allow_open_path=args.open_path,
        dedupe=not args.no_dedupe,
    )
    if not result.points:
        print("Nothing to write (no segments after filtering).", file=sys.stderr)
        return 2

    write_svg(root, result.points, args.output, args.stroke_width * uu_per_mm)
    if not args.quiet:
        print(format_report(result.stats, units_per_mm=uu_per_mm))
    if args.png_before:
        render_png(polylines, args.png_before)
    if args.png_after:
        render_png([result.points], args.png_after, highlight=highlight_segments(result),
                   bbox=bbox_of_polylines(polylines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
