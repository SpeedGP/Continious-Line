#!/usr/bin/env python3
# coding=utf-8
"""
Continuous Line -- Inkscape 1.4 extension.

Replaces the selection (or the whole drawing) with ONE path containing a
single subpath that covers every line of the original, so that a G-code
generator with no pen-lift (e.g. J Tech Photonics Laser Tool) emits no rapid
moves between shapes.

This file is only the inkex glue: it flattens the SVG geometry into
polylines in document user units, hands them to ``cl_pipeline.run_pipeline``
(pure Python, no Inkscape dependency) and writes the result back.
"""

import math
import os
import sys

# Make sure the sibling modules are importable no matter how Inkscape
# launches us (cwd is not guaranteed to be the extension folder).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inkex  # noqa: E402
from inkex import PathElement, Transform  # noqa: E402
from lxml import etree  # noqa: E402  (ships with Inkscape's Python)

from cl_geometry import flatten_cubic, user_units_per_mm  # noqa: E402
from cl_pipeline import format_report, run_pipeline  # noqa: E402

SHAPE_TAGS = ("path", "rect", "circle", "ellipse", "line", "polyline", "polygon")
CONTAINER_TAGS = ("g", "svg", "a", "switch")
SKIP_TAGS = ("defs", "metadata", "namedview", "title", "desc", "style", "clipPath",
             "mask", "marker", "pattern", "symbol", "linearGradient", "radialGradient",
             "filter", "script", "grid", "guide", "page", "perspective")
TEXT_TAGS = ("text", "tspan", "flowRoot", "flowPara", "flowRegion", "textPath")

OUTPUT_ID = "continuous-line"


def _tag(elem):
    try:
        return etree.QName(elem.tag).localname
    except (ValueError, TypeError):
        return ""


def _is_hidden(elem):
    try:
        style = elem.style
        if style.get("display") == "none" or style.get("visibility") == "hidden":
            return True
    except Exception:  # pylint: disable=broad-except
        pass
    return elem.get("display") == "none" or elem.get("visibility") == "hidden"


def _scale_of(tr):
    det = abs(tr.a * tr.d - tr.b * tr.c)
    return math.sqrt(det) if det > 0 else 1.0


class ContinuousLine(inkex.EffectExtension):
    """Merge all selected geometry into a single continuous path."""

    def add_arguments(self, pars):
        pars.add_argument("--tab")
        pars.add_argument("--flatten_tol", type=float, default=0.1,
                          help="curve flatten tolerance (mm)")
        pars.add_argument("--snap_tol", type=float, default=0.1,
                          help="merge/snap tolerance (mm)")
        pars.add_argument("--start_corner", default="bottom-left")
        pars.add_argument("--keep_original", type=inkex.Boolean, default=False)
        pars.add_argument("--min_island", type=float, default=0.0,
                          help="drop closed contours smaller than this (mm)")
        pars.add_argument("--bridge_style", default="straight")
        pars.add_argument("--allow_open", type=inkex.Boolean, default=False,
                          help="allow an Euler path (end anywhere) instead of a circuit")
        pars.add_argument("--stroke_width", type=float, default=0.3,
                          help="stroke width of the result (mm)")

    # ------------------------------------------------------------------
    # units
    # ------------------------------------------------------------------
    def _uu_per_mm(self):
        """User units per millimetre, honouring the viewBox scale."""
        svg = self.svg
        for name in ("viewport_to_unit", "unittouu"):
            fn = getattr(svg, name, None)
            if fn is None:
                continue
            try:
                v = float(fn("1mm"))
                if v > 0 and math.isfinite(v):
                    return v
            except Exception:  # pylint: disable=broad-except
                continue
        return user_units_per_mm(svg.get("width"), svg.get("viewBox"))

    # ------------------------------------------------------------------
    # geometry collection
    # ------------------------------------------------------------------
    def _collect(self, roots, flatten_tol):
        """Flatten *roots* (and descendants) into polylines in root user units.

        Returns (polylines, leaf_elements, warnings).
        """
        polylines = []
        leaves = []
        warnings = []
        seen = set()

        def walk(elem, parent_tr, depth, record=True):
            """*record*: whether leaf shapes found here are the user's own
            objects (to delete/hide afterwards).  False while expanding the
            target of a <use>, because the target may live in <defs> or be
            shared by other clones."""
            if depth > 64:
                return
            tag = _tag(elem)
            if tag in SKIP_TAGS or tag.startswith("sodipodi") or isinstance(elem, etree._Comment):
                return
            if _is_hidden(elem):
                return
            try:
                tr = parent_tr @ elem.transform
            except Exception:  # pylint: disable=broad-except
                tr = parent_tr

            if tag in CONTAINER_TAGS:
                for child in list(elem):
                    walk(child, tr, depth + 1, record)
                return
            if tag in SHAPE_TAGS:
                if record:
                    if id(elem) in seen:          # selected twice (parent + child)
                        return
                    seen.add(id(elem))
                pls = self._shape_polylines(elem, tr, flatten_tol)
                if pls is None:
                    warnings.append("<%s id=%s> could not be converted; skipped"
                                    % (tag, elem.get_id()))
                    return
                polylines.extend(pls)
                if record:
                    leaves.append(elem)
                return
            if tag == "use":
                ref = None
                try:
                    ref = elem.href
                except Exception:  # pylint: disable=broad-except
                    ref = None
                if ref is None:
                    warnings.append("clone <use id=%s> skipped: target not found "
                                    "(unlink clones with Shift+Alt+D)" % elem.get_id())
                    return
                try:
                    x = float(elem.get("x", 0) or 0)
                    y = float(elem.get("y", 0) or 0)
                except ValueError:
                    x = y = 0.0
                # Clone geometry = use.transform * translate(x,y) * target's
                # own transform (not the target's ancestors).
                before = len(polylines)
                walk(ref, tr @ Transform(translate=(x, y)), depth + 1, False)
                if record and len(polylines) > before:
                    leaves.append(elem)           # the clone itself goes away
                return
            if tag in TEXT_TAGS:
                warnings.append("text object <%s id=%s> skipped: convert text to path "
                                "first (Path > Object to Path, Shift+Ctrl+C)" % (tag, elem.get_id()))
                return
            if tag == "image":
                warnings.append("<image id=%s> skipped: trace it to paths first "
                                "(Path > Trace Bitmap)" % elem.get_id())
                return
            # anything else (foreignObject, unknown) -> ignore silently

        for root in roots:
            parent = root.getparent()
            try:
                parent_tr = parent.composed_transform() if parent is not None else Transform()
            except Exception:  # pylint: disable=broad-except
                parent_tr = Transform()
            walk(root, parent_tr, 0)

        leaves = [e for e in leaves if e is not None]
        return polylines, leaves, warnings

    def _shape_polylines(self, elem, tr, flatten_tol):
        """Flatten one shape element into polylines in root user units."""
        try:
            path = elem.path
        except Exception:  # pylint: disable=broad-except
            return None
        if path is None:
            return None
        try:
            csp = path.to_superpath()
        except Exception:  # pylint: disable=broad-except
            return None
        apply = tr.apply_to_point
        out = []
        for sub in csp:
            if not sub:
                continue
            # Transform all control points first: affine maps keep cubics
            # cubic, so flattening afterwards honours the document tolerance.
            pts = []
            for h1, p, h2 in sub:
                a = apply((h1[0], h1[1]))
                b = apply((p[0], p[1]))
                c = apply((h2[0], h2[1]))
                pts.append(((a.x, a.y), (b.x, b.y), (c.x, c.y)))
            poly = [pts[0][1]]
            for i in range(1, len(pts)):
                p0 = pts[i - 1][1]
                c1 = pts[i - 1][2]
                c2 = pts[i][0]
                p3 = pts[i][1]
                poly.extend(flatten_cubic(p0, c1, c2, p3, flatten_tol))
            out.append(poly)
        return out

    # ------------------------------------------------------------------
    # main
    # ------------------------------------------------------------------
    def effect(self):
        svg = self.svg
        opt = self.options
        uu_per_mm = self._uu_per_mm()
        flatten_tol = max(opt.flatten_tol, 0.001) * uu_per_mm
        snap_tol = max(opt.snap_tol, 0.0) * uu_per_mm
        min_island = max(opt.min_island, 0.0) * uu_per_mm

        selected = list(svg.selection.values())
        if selected:
            roots = selected
            scope = "selection (%d objects)" % len(selected)
        else:
            roots = [c for c in svg if _tag(c) not in SKIP_TAGS]
            scope = "whole document"

        polylines, leaves, warnings = self._collect(roots, flatten_tol)
        if not polylines:
            self.msg("Continuous Line: no drawable geometry found in the %s." % scope)
            for w in warnings:
                self.msg("  " + w)
            return

        result = run_pipeline(
            polylines,
            snap_tol=snap_tol,
            start_corner=opt.start_corner,
            min_island=min_island,
            allow_open_path=bool(opt.allow_open),
            dedupe=True,
            bridge_style=opt.bridge_style or "straight",
        )
        if not result.points:
            self.msg("Continuous Line: nothing left after filtering (all contours "
                     "smaller than the minimum island size?).")
            return

        # ---- remove or hide the originals
        if opt.keep_original:
            layer = self._original_layer()
            for elem in leaves:
                parent = elem.getparent()
                if parent is None:
                    continue
                try:
                    elem.transform = parent.composed_transform() @ elem.transform
                except Exception:  # pylint: disable=broad-except
                    pass
                layer.append(elem)
        else:
            emptied = set()
            for elem in leaves:
                parent = elem.getparent()
                if parent is not None:
                    emptied.add(parent)
                    parent.remove(elem)
            # prune groups we emptied completely (but never layers / root)
            for grp in emptied:
                while (grp is not None and _tag(grp) == "g" and len(grp) == 0
                       and grp.get("inkscape:groupmode") != "layer"):
                    parent = grp.getparent()
                    if parent is None:
                        break
                    parent.remove(grp)
                    grp = parent

        # ---- write the single path on the current layer
        layer = svg.get_current_layer()
        if layer is None:
            layer = svg
        try:
            inv = -layer.composed_transform()
        except Exception:  # pylint: disable=broad-except
            inv = Transform()
        pts = result.points
        if inv != Transform():
            ap = inv.apply_to_point
            pts = [(q.x, q.y) for q in (ap(p) for p in pts)]

        new = PathElement()
        new.set("d", self._points_to_d(pts))
        new.style = inkex.Style({
            "fill": "none",
            "stroke": "#000000",
            "stroke-width": "%.4g" % (max(opt.stroke_width, 0.01) * uu_per_mm / _scale_of(layer.composed_transform())),
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
        })
        new.set("inkscape:label", "Continuous Line")
        existing = svg.getElementById(OUTPUT_ID)
        new.set("id", OUTPUT_ID if existing is None else svg.get_unique_id(OUTPUT_ID + "-"))
        layer.append(new)

        # ---- report
        lines = [format_report(result.stats, units_per_mm=uu_per_mm, unit_label="mm"),
                 "  scope:                 %s" % scope,
                 "  start point:           (%.1f, %.1f) mm from top-left" % (
                     result.stats["start_point"][0] / uu_per_mm,
                     result.stats["start_point"][1] / uu_per_mm)]
        if result.stats.get("open_path"):
            lines.append("  end point:             (%.1f, %.1f) mm" % (
                result.stats["end_point"][0] / uu_per_mm,
                result.stats["end_point"][1] / uu_per_mm))
        if warnings:
            lines.append("Warnings:")
            lines.extend("  " + w for w in warnings[:30])
            if len(warnings) > 30:
                lines.append("  ... and %d more" % (len(warnings) - 30))
        self.msg("\n".join(lines))

    # ------------------------------------------------------------------
    def _original_layer(self):
        for elem in self.svg:
            if _tag(elem) == "g" and elem.get("inkscape:groupmode") == "layer" \
                    and elem.get("inkscape:label") == "Original":
                layer = elem
                break
        else:
            layer = inkex.Layer()
            layer.set("inkscape:groupmode", "layer")
            layer.set("inkscape:label", "Original")
            self.svg.append(layer)
        layer.set("style", "display:none")
        return layer

    @staticmethod
    def _points_to_d(points, precision=3):
        """Exactly one subpath: absolute M, then L pairs, no Z."""
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


if __name__ == "__main__":
    ContinuousLine().run()
