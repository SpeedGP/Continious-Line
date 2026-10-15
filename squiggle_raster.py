#!/usr/bin/env python3
# coding=utf-8
"""
Squiggle Raster -- Inkscape 1.4 extension.

Select a bitmap (<image>) and this replaces it with ONE path: a single
continuous sine-wave "squiggle" line whose amplitude and frequency follow
the darkness of the picture, rows walked boustrophedon.  Zero pen lifts,
zero bridges -- the output is ready for a no-pen-lift plotter and can go
straight to the J Tech Photonics Laser Tool.

The rendering lives in cl_squiggle.py (pure Python); this file is the
inkex glue: locate the image data, map its box into document coordinates,
write the path on the current layer, report.
"""

import math
import os
import sys
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inkex  # noqa: E402
from inkex import PathElement, Transform  # noqa: E402
from lxml import etree  # noqa: E402

from cl_geometry import points_to_d, user_units_per_mm  # noqa: E402
from cl_squiggle import GrayImage, SquiggleParams, format_squiggle_report, squiggle  # noqa: E402

OUTPUT_ID = "squiggle-raster"


def _tag(elem):
    try:
        return etree.QName(elem.tag).localname
    except (ValueError, TypeError):
        return ""


def _scale_of(tr):
    det = abs(tr.a * tr.d - tr.b * tr.c)
    return math.sqrt(det) if det > 0 else 1.0


class SquiggleRaster(inkex.EffectExtension):
    """Render a selected bitmap as one continuous squiggle line."""

    def add_arguments(self, pars):
        pars.add_argument("--tab")
        pars.add_argument("--rows", type=int, default=80)
        pars.add_argument("--amp_min", type=float, default=0.0)
        pars.add_argument("--amp_max", type=float, default=90.0)
        pars.add_argument("--freq_min", type=float, default=0.3)
        pars.add_argument("--freq_max", type=float, default=2.5)
        pars.add_argument("--gamma", type=float, default=1.0)
        pars.add_argument("--white_cutoff", type=float, default=4.0)
        pars.add_argument("--invert", type=inkex.Boolean, default=False)
        pars.add_argument("--direction", default="horizontal")
        pars.add_argument("--tolerance", type=float, default=0.1)
        pars.add_argument("--feed", type=float, default=2000.0)
        pars.add_argument("--keep_image", type=inkex.Boolean, default=False)
        pars.add_argument("--stroke_width", type=float, default=0.3)

    # ------------------------------------------------------------------
    def _uu_per_mm(self):
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

    def _load_image(self, elem):
        """Return a GrayImage for an <image> element (embedded or linked)."""
        href = elem.get("xlink:href") or elem.get("href") or ""
        if href.startswith("data:"):
            return GrayImage.from_data_uri(href)
        candidates = []
        absref = elem.get("sodipodi:absref")
        if absref:
            candidates.append(absref)
        path = href
        if path.lower().startswith("file:"):
            path = url2pathname(urlparse(path).path)
        else:
            path = unquote(path)
        if path:
            candidates.append(path)
            if not os.path.isabs(path):
                doc = os.environ.get("DOCUMENT_PATH") or ""
                if doc:
                    candidates.append(os.path.join(os.path.dirname(doc), path))
                try:
                    candidates.append(self.absolute_href(path))
                except Exception:  # pylint: disable=broad-except
                    pass
        for cand in candidates:
            if cand and os.path.isfile(cand):
                return GrayImage.from_file(cand)
        raise inkex.AbortExtension(
            "Could not read the image file for <image id=%s> (href=%r). Embed the image "
            "(Extensions > Images > Embed Images) and try again." % (elem.get_id(), href[:80]))

    # ------------------------------------------------------------------
    def effect(self):
        svg = self.svg
        opt = self.options
        uu_per_mm = self._uu_per_mm()

        images = [e for e in svg.selection.values() if _tag(e) == "image"]
        if not images:
            # fall back: a selected group containing exactly one image, or the
            # only image in the document
            for e in svg.selection.values():
                images.extend(x for x in e.iter() if _tag(x) == "image")
            if not images:
                images = [x for x in svg.iter() if _tag(x) == "image"]
        if len(images) != 1:
            raise inkex.AbortExtension(
                "Select exactly one bitmap image (found %d)." % len(images))
        img_elem = images[0]
        gray = self._load_image(img_elem)

        # ---- image box in its own local coordinates (before transforms)
        try:
            bx = float(img_elem.get("x", 0) or 0)
            by = float(img_elem.get("y", 0) or 0)
            bw = float(img_elem.get("width", 0) or 0)
            bh = float(img_elem.get("height", 0) or 0)
        except ValueError:
            raise inkex.AbortExtension("Image has a non-numeric x/y/width/height.")
        if bw <= 0 or bh <= 0:
            bw, bh = float(gray.width), float(gray.height)
        # default preserveAspectRatio = xMidYMid meet -> letterbox the picture
        par = (img_elem.get("preserveAspectRatio") or "xMidYMid meet").strip()
        if "none" not in par and gray.width and gray.height:
            s = min(bw / gray.width, bh / gray.height)
            dw, dh = gray.width * s, gray.height * s
            bx += (bw - dw) / 2.0
            by += (bh - dh) / 2.0
            bw, bh = dw, dh

        tr = img_elem.composed_transform()
        scale = _scale_of(tr)          # local units -> document units
        tol_local = (max(opt.tolerance, 0.005) * uu_per_mm) / scale

        params = SquiggleParams(
            rows=opt.rows,
            amp_min=opt.amp_min / 100.0,
            amp_max=opt.amp_max / 100.0,
            freq_min=opt.freq_min,
            freq_max=opt.freq_max,
            gamma=opt.gamma,
            invert=bool(opt.invert),
            white_cutoff=opt.white_cutoff / 100.0,
            tolerance=tol_local,
            vertical=(opt.direction == "vertical"),
        )
        pts, info = squiggle(gray, bw, bh, params)
        if not pts:
            raise inkex.AbortExtension("Nothing rendered (empty image?).")

        # ---- local -> document -> current layer coordinates
        layer = svg.get_current_layer()
        if layer is None:
            layer = svg
        try:
            to_layer = (-layer.composed_transform()) @ tr
        except Exception:  # pylint: disable=broad-except
            to_layer = tr
        ap = to_layer.apply_to_point
        out = []
        for x, y in pts:
            q = ap((bx + x, by + y))
            out.append((q.x, q.y))

        # ---- image: delete or hide
        if opt.keep_image:
            try:
                img_elem.style["display"] = "none"
            except Exception:  # pylint: disable=broad-except
                img_elem.set("style", (img_elem.get("style") or "") + ";display:none")
        else:
            parent = img_elem.getparent()
            if parent is not None:
                parent.remove(img_elem)

        new = PathElement()
        new.set("d", points_to_d(out))
        new.style = inkex.Style({
            "fill": "none",
            "stroke": "#000000",
            "stroke-width": "%.4g" % (max(opt.stroke_width, 0.01) * uu_per_mm
                                      / _scale_of(layer.composed_transform())),
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
        })
        new.set("inkscape:label", "Squiggle Raster")
        existing = svg.getElementById(OUTPUT_ID)
        new.set("id", OUTPUT_ID if existing is None else svg.get_unique_id(OUTPUT_ID + "-"))
        layer.append(new)

        # report lengths in mm: info is in local units -> * scale -> / uu_per_mm
        for key in ("pitch", "length", "width", "height"):
            info[key] *= scale
        self.msg(format_squiggle_report(info, units_per_mm=uu_per_mm, feed_mm_min=opt.feed))


if __name__ == "__main__":
    SquiggleRaster().run()
