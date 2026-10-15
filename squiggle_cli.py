#!/usr/bin/env python3
"""
squiggle_cli.py -- render a photo as one continuous squiggle line, no Inkscape.

    python squiggle_cli.py photo.jpg out.svg --width-mm 1900 --rows 100 [--png preview.png]

Writes an SVG (mm units) containing a single path, ready for the J Tech
Photonics Laser Tool or any other G-code generator.  JPEG input needs
Pillow; 8-bit PNG works with the standard library alone.
"""

import argparse
import sys
import xml.etree.ElementTree as ET

from cl_geometry import points_to_d
from cl_squiggle import GrayImage, SquiggleParams, format_squiggle_report, squiggle


def main(argv=None):
    ap = argparse.ArgumentParser(description="Photo -> single continuous squiggle line SVG.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--width-mm", type=float, default=200.0, help="output width in mm (height follows aspect)")
    ap.add_argument("--rows", type=int, default=80)
    ap.add_argument("--amp-max", type=float, default=0.9, help="peak-to-peak amplitude in black, fraction of pitch")
    ap.add_argument("--amp-min", type=float, default=0.0)
    ap.add_argument("--freq-max", type=float, default=2.5, help="waves per pitch in black")
    ap.add_argument("--freq-min", type=float, default=0.3)
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("--white-cutoff", type=float, default=0.04)
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--vertical", action="store_true")
    ap.add_argument("--tolerance", type=float, default=0.1, help="chord tolerance, mm")
    ap.add_argument("--feed", type=float, default=2000.0, help="feed rate for the time estimate, mm/min")
    ap.add_argument("--stroke-width", type=float, default=0.3)
    ap.add_argument("--png", help="write a preview PNG (needs Pillow)")
    ap.add_argument("--png-width", type=int, default=1400)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    img = GrayImage.from_file(args.input)
    width = args.width_mm
    height = width * img.height / img.width
    params = SquiggleParams(rows=args.rows, amp_min=args.amp_min, amp_max=args.amp_max,
                            freq_min=args.freq_min, freq_max=args.freq_max, gamma=args.gamma,
                            invert=args.invert, white_cutoff=args.white_cutoff,
                            tolerance=args.tolerance, vertical=args.vertical)
    pts, info = squiggle(img, width, height, params)

    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": "%gmm" % width, "height": "%gmm" % height,
        "viewBox": "0 0 %g %g" % (width, height),
    })
    path = ET.SubElement(svg, "path")
    path.set("id", "squiggle-raster")
    path.set("d", points_to_d(pts))
    path.set("style", "fill:none;stroke:#000;stroke-width:%g;stroke-linecap:round;"
                      "stroke-linejoin:round" % args.stroke_width)
    ET.ElementTree(svg).write(args.output, encoding="utf-8", xml_declaration=True)
    if not args.quiet:
        print(format_squiggle_report(info, feed_mm_min=args.feed))

    if args.png:
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            print("Pillow not installed; no preview written", file=sys.stderr)
            return 0
        scale = args.png_width / width
        im = Image.new("L", (args.png_width, int(height * scale) + 1), 255)
        draw = ImageDraw.Draw(im)
        draw.line([(x * scale, y * scale) for x, y in pts], fill=0,
                  width=max(1, int(round(args.stroke_width * scale))))
        im.save(args.png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
