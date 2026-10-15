"""End-to-end tests: sample SVG -> polylines -> pipeline -> single-subpath SVG.

Uses the CLI's stdlib SVG reader (not inkex), so it runs anywhere.
"""
import math
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cl_cli  # noqa: E402
from cl_geometry import user_units_per_mm  # noqa: E402
from cl_graph import KIND_ORIG, SpatialHash  # noqa: E402
from cl_pipeline import filter_polylines, format_report, run_pipeline  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
SAMPLE_FILES = ["traced_bitmap.svg", "text_as_paths.svg", "clip_art.svg"]


def load(name, flatten_mm=0.1):
    root = ET.parse(os.path.join(SAMPLES, name)).getroot()
    uu = user_units_per_mm(root.get("width"), root.get("viewBox"))
    polylines, warnings = cl_cli.collect_polylines(root, flatten_mm * uu)
    return root, uu, polylines, warnings


def count_subpaths(d):
    return len(re.findall(r"[Mm]", d))


def assert_covers_input(result, polylines, snap_tol, min_island=0.0):
    """Every input segment (that the pipeline did not deliberately filter
    out: single points, zero-size, small islands) must correspond to a step
    of the output walk."""
    polylines, _, _ = filter_polylines(polylines, min_island=min_island, closed_tol=snap_tol)
    g = result.graph
    grid = SpatialHash(max(snap_tol, 1e-9))
    for nid, (x, y) in enumerate(g.nodes):
        grid.insert(x, y, nid)

    def node_of(p):
        best, bd = -1, (snap_tol * 1.0001) ** 2
        for nid in grid.neighbourhood(p[0], p[1]):
            x, y = g.nodes[nid]
            d = (x - p[0]) ** 2 + (y - p[1]) ** 2
            if d <= bd:
                bd, best = d, nid
        return best

    steps = set()
    seq = result.node_sequence
    for a, b in zip(seq, seq[1:]):
        steps.add((a, b) if a < b else (b, a))
    missing = 0
    for pl in polylines:
        for p, q in zip(pl, pl[1:]):
            u, v = node_of(p), node_of(q)
            assert u >= 0 and v >= 0, "input vertex not snapped to any node"
            if u == v:
                continue                       # zero-length after snapping
            if ((u, v) if u < v else (v, u)) not in steps:
                missing += 1
    assert missing == 0, "%d input segments are not drawn" % missing


def assert_multiplicity(result):
    """No (u,v) pair is walked more often than the eulerized graph has edges
    for it, and each is walked at least once."""
    g = result.graph
    have = {}
    for u, v in zip(g.eu, g.ev):
        key = (u, v) if u < v else (v, u)
        have[key] = have.get(key, 0) + 1
    walked = {}
    seq = result.node_sequence
    for a, b in zip(seq, seq[1:]):
        key = (a, b) if a < b else (b, a)
        walked[key] = walked.get(key, 0) + 1
    assert walked == have


def run_sample(name, **kw):
    root, uu, polylines, warnings = load(name)
    assert polylines, "sample has no geometry"
    snap = kw.pop("snap_tol", 0.1 * uu)
    result = run_pipeline(polylines, snap_tol=snap, **kw)
    print("\n== %s\n%s" % (name, format_report(result.stats, units_per_mm=uu)))
    for w in warnings:
        print("   warning:", w)

    # one polyline, one subpath
    assert len(result.points) >= 2
    d = cl_cli.points_to_d(result.points)
    assert count_subpaths(d) == 1
    assert "Z" not in d and "z" not in d

    assert_covers_input(result, polylines, snap, min_island=kw.get("min_island", 0.0))
    assert_multiplicity(result)

    # written SVG re-parses to exactly one polyline with the same point count
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.svg")
        cl_cli.write_svg(root, result.points, out, 0.3 * uu)
        root2 = ET.parse(out).getroot()
        paths = [el for el in root2.iter() if el.tag.endswith("path")]
        assert len(paths) == 1
        assert paths[0].get("id") == "continuous-line"
        pls2, _ = cl_cli.collect_polylines(root2, 0.1 * uu)
        assert len(pls2) == 1
        assert len(pls2[0]) == len(result.points)
    return result, warnings


def test_traced_bitmap():
    r, _ = run_sample("traced_bitmap.svg")
    assert r.stats["components"] >= 10          # lots of islands
    assert r.stats["bridges"] == r.stats["components"] - 1
    assert r.stats["overhead_pct"] < 40


def test_traced_bitmap_min_island_and_dedupe_off():
    r, _ = run_sample("traced_bitmap.svg", min_island=2.0)
    assert r.stats["dropped_islands"] >= 3
    r2, _ = run_sample("traced_bitmap.svg", dedupe=False)
    assert r2.stats["duplicates_removed"] == 0


def test_text_as_paths():
    r, _ = run_sample("text_as_paths.svg")
    assert r.stats["components"] == 10          # 5 glyphs, several with holes
    assert r.stats["bridges"] == 9
    assert r.stats["overhead_pct"] < 25


def test_clip_art_with_degenerate_and_skipped_objects():
    r, warnings = run_sample("clip_art.svg")
    joined = " ".join(warnings)
    assert "use" in joined and "text" in joined
    assert r.stats["zero_length_dropped"] >= 1
    assert r.stats["duplicates_removed"] >= 1
    assert r.stats["dropped_degenerate"] >= 1
    r_open, _ = run_sample("clip_art.svg", allow_open_path=True)
    assert r_open.stats["open_path"]
    assert r_open.stats["retrace_length"] <= r.stats["retrace_length"]


def test_all_corners_all_samples():
    for name in SAMPLE_FILES:
        root, uu, polylines, _ = load(name)
        for corner in ("bottom-left", "top-left", "top-right", "bottom-right"):
            r = run_pipeline(polylines, snap_tol=0.1 * uu, start_corner=corner)
            xs = [p[0] for p in r.points]
            ys = [p[1] for p in r.points]
            sx, sy = r.points[0]
            # the start must be within the half of the bbox nearest the corner
            midx, midy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            assert (sx <= midx) == ("left" in corner), (name, corner)
            assert (sy >= midy) == ("bottom" in corner), (name, corner)


def test_cli_main_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "o.svg")
        rc = cl_cli.main([os.path.join(SAMPLES, "text_as_paths.svg"), out, "--quiet"])
        assert rc == 0
        d = [el for el in ET.parse(out).getroot().iter() if el.tag.endswith("path")][0].get("d")
        assert count_subpaths(d) == 1


def test_viewbox_scaling_px_document():
    # 100mm wide document expressed in px user units: 0.1 mm snap = 0.378 px
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" '
           'viewBox="0 0 377.95 377.95"><path d="M 0,0 h 100 v 100 h -100 z"/>'
           '<path d="M 100.2,0 h 100 v 100 h -100 z"/></svg>')
    root = ET.fromstring(svg)
    uu = user_units_per_mm(root.get("width"), root.get("viewBox"))
    assert math.isclose(uu, 3.7795, rel_tol=1e-3)
    pls, _ = cl_cli.collect_polylines(root, 0.1 * uu)
    r = run_pipeline(pls, snap_tol=0.1 * uu)          # 0.2 px gap < 0.378 px -> merged
    assert r.stats["components"] == 1
    r2 = run_pipeline(pls, snap_tol=0.01 * uu)
    assert r2.stats["components"] == 2


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
