"""
cl_pipeline.py -- the Inkscape-independent core of the Continuous Line extension.

    from cl_pipeline import run_pipeline
    result = run_pipeline(polylines, snap_tol=0.1, start_corner="bottom-left")
    result.points   # one polyline: [(x, y), ...]
    result.stats    # dict, see format_report()

Everything is in the caller's coordinate units (the Inkscape wrapper passes
document user units and converts millimetre options before calling).

The same function can later be wrapped as a G-code post-processor: parse the
G1 moves into polylines, run the pipeline, emit one G1 chain.
"""

import math
import time

from cl_geometry import bbox_of_polylines, bbox_of_points, corner_point
from cl_graph import (
    KIND_BRIDGE, KIND_ORIG, KIND_RETRACE,
    bridge_components, build_graph, connected_components, euler_circuit, eulerize,
)

BRIDGE_STYLES = ("straight",)   # "route" (along existing lines) is a future option


class Result:
    """Output of :func:`run_pipeline`."""

    def __init__(self, points, stats, graph, node_sequence, start, end):
        self.points = points              # list of (x, y) -- ONE polyline
        self.stats = stats                # dict of numbers (see format_report)
        self.graph = graph                # cl_graph.Graph after eulerizing
        self.node_sequence = node_sequence
        self.start = start
        self.end = end


def filter_polylines(polylines, min_island=0.0, closed_tol=0.0):
    """Drop unusable polylines.

    * fewer than 2 points -> dropped (counted as ``degenerate``)
    * all points identical -> dropped (``degenerate``)
    * closed contour whose bounding box is smaller than *min_island* in both
      directions -> dropped (``islands``).  "Closed" means the first and last
      point coincide within *closed_tol*.
    Returns (kept, dropped_degenerate, dropped_islands).
    """
    kept = []
    degenerate = 0
    islands = 0
    for pl in polylines:
        if len(pl) < 2:
            degenerate += 1
            continue
        bb = bbox_of_points(pl)
        if bb[2] - bb[0] <= 0 and bb[3] - bb[1] <= 0:
            degenerate += 1
            continue
        if min_island > 0:
            closed = math.hypot(pl[0][0] - pl[-1][0], pl[0][1] - pl[-1][1]) <= closed_tol
            if closed and (bb[2] - bb[0]) < min_island and (bb[3] - bb[1]) < min_island:
                islands += 1
                continue
        kept.append(pl)
    return kept, degenerate, islands


def run_pipeline(polylines, snap_tol=0.1, start_corner="bottom-left", min_island=0.0,
                 allow_open_path=False, dedupe=True, bridge_style="straight",
                 k_nearest=6):
    """Merge *polylines* into a single continuous polyline.

    Parameters (all lengths in the same units as the input points):
      snap_tol         vertices closer than this become one graph node
      start_corner     'bottom-left' | 'top-left' | 'top-right' | 'bottom-right'
                       (SVG convention: bottom == larger y)
      min_island       drop closed contours smaller than this in both axes
      allow_open_path  if True the walk may end somewhere other than where it
                       started (Euler *path* instead of circuit) -- saves one
                       retrace path; the start is then the odd node nearest
                       the chosen corner
      dedupe           collapse exactly duplicated segments
      bridge_style     'straight' (only option today; hook for 'route')
      k_nearest        candidate odd neighbours per odd node in the matching
    """
    if bridge_style not in BRIDGE_STYLES:
        raise ValueError("unknown bridge_style %r; choose one of %s" % (bridge_style, BRIDGE_STYLES))

    t0 = time.perf_counter()
    polys, degenerate, islands = filter_polylines(polylines, min_island, closed_tol=snap_tol)

    g, bstats = build_graph(polys, snap_tol, dedupe=dedupe)
    t1 = time.perf_counter()
    stats = {
        "input_polylines": len(polylines),
        "dropped_degenerate": degenerate,
        "dropped_islands": islands,
    }
    stats.update(bstats)

    if g.num_edges() == 0:
        stats.update({
            "components": 0, "bridges": 0, "bridge_length": 0.0, "odd_nodes": 0,
            "retrace_edges": 0, "retrace_length": 0.0, "drawing_length": 0.0,
            "final_length": 0.0, "overhead_length": 0.0, "overhead_pct": 0.0,
            "output_points": 0, "open_path": False, "time_s": time.perf_counter() - t0,
        })
        return Result([], stats, g, [], None, None)

    comp, ncomp = connected_components(g)
    bridge_eids = bridge_components(g, comp, ncomp, bridge_style=bridge_style)
    t2 = time.perf_counter()

    bbox = g.bbox()
    corner = corner_point(bbox, start_corner)
    odd_before = len(g.odd_nodes())
    retrace_eids, start, end = eulerize(g, allow_open=allow_open_path, start_hint=corner,
                                        k_nearest=k_nearest)
    t3 = time.perf_counter()

    seq = euler_circuit(g, start)
    t4 = time.perf_counter()

    nodes = g.nodes
    points = [nodes[u] for u in seq]
    by_kind = g.length_by_kind()
    drawing_len = by_kind[KIND_ORIG]
    bridge_len = by_kind[KIND_BRIDGE]
    retrace_len = by_kind[KIND_RETRACE]
    final_len = drawing_len + bridge_len + retrace_len
    overhead = bridge_len + retrace_len

    stats.update({
        "components": ncomp,
        "bridges": len(bridge_eids),
        "bridge_length": bridge_len,
        "odd_nodes": odd_before,
        "retrace_edges": len(retrace_eids),
        "retrace_length": retrace_len,
        "drawing_length": drawing_len,
        "final_length": final_len,
        "overhead_length": overhead,
        "overhead_pct": (100.0 * overhead / final_len) if final_len > 0 else 0.0,
        "output_points": len(points),
        "open_path": start != end,
        "start_corner": start_corner,
        "start_point": nodes[start],
        "end_point": nodes[end],
        "time_s": t4 - t0,
        "time_build_s": t1 - t0,
        "time_bridge_s": t2 - t1,
        "time_eulerize_s": t3 - t2,
        "time_walk_s": t4 - t3,
    })
    return Result(points, stats, g, seq, start, end)


def format_report(stats, units_per_mm=1.0, unit_label="mm"):
    """Human-readable summary.  Lengths are divided by *units_per_mm*."""
    def L(v):
        return "%.1f %s" % (v / units_per_mm, unit_label)

    if stats.get("edges", 0) == 0:
        return ("Continuous Line: nothing to do -- no usable segments found "
                "(%d polylines, %d degenerate, %d islands dropped)."
                % (stats.get("input_polylines", 0), stats.get("dropped_degenerate", 0),
                   stats.get("dropped_islands", 0)))

    lines = [
        "Continuous Line report",
        "  input segments:        %d  (%d polylines; %d zero-length, %d duplicate, "
        "%d single-point dropped)" % (
            stats["input_segments"], stats["input_polylines"], stats["zero_length_dropped"],
            stats["duplicates_removed"], stats["dropped_degenerate"] + stats["isolated_points_dropped"]),
    ]
    if stats.get("dropped_islands"):
        lines.append("  small islands dropped: %d" % stats["dropped_islands"])
    lines += [
        "  graph:                 %d nodes, %d edges, %d odd nodes" % (
            stats["nodes"], stats["edges"], stats["odd_nodes"]),
        "  components found:      %d" % stats["components"],
        "  bridge lines added:    %d, total %s" % (stats["bridges"], L(stats["bridge_length"])),
        "  retraced:              %d edges, total %s" % (stats["retrace_edges"], L(stats["retrace_length"])),
        "  drawing length:        %s" % L(stats["drawing_length"]),
        "  final path length:     %s  (%d points, %s)" % (
            L(stats["final_length"]), stats["output_points"],
            "open path" if stats["open_path"] else "closed circuit"),
        "  overhead:              %s = %.1f%% of the final path" % (
            L(stats["overhead_length"]), stats["overhead_pct"]),
        "  time:                  %.2f s" % stats["time_s"],
    ]
    return "\n".join(lines)
