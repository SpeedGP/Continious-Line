"""Unit tests for cl_graph / cl_pipeline on small synthetic inputs.

Run with  python -m pytest  (from the continuous_line folder)
or        python tests/test_graph.py   (no pytest needed).
"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cl_graph import (  # noqa: E402
    KIND_BRIDGE, KIND_ORIG, KIND_RETRACE, Graph, SpatialHash, bridge_components,
    build_graph, connected_components, euler_circuit, eulerize,
)
from cl_pipeline import filter_polylines, format_report, run_pipeline  # noqa: E402

EPS = 1e-6


def square(x, y, s):
    return [(x, y), (x + s, y), (x + s, y + s), (x, y + s), (x, y)]


def close(a, b, eps=EPS):
    return abs(a - b) <= eps


def check_walk(result):
    """Generic validity: one polyline, consecutive points joined by an edge of
    the eulerized graph, every edge used exactly once."""
    g = result.graph
    seq = result.node_sequence
    assert len(seq) == g.num_edges() + 1
    assert len(result.points) == len(seq)
    # multiset of edges in the graph == multiset of steps in the walk
    graph_pairs = {}
    for u, v in zip(g.eu, g.ev):
        key = (u, v) if u < v else (v, u)
        graph_pairs[key] = graph_pairs.get(key, 0) + 1
    walk_pairs = {}
    for a, b in zip(seq, seq[1:]):
        assert a != b, "zero-length step in walk"
        key = (a, b) if a < b else (b, a)
        walk_pairs[key] = walk_pairs.get(key, 0) + 1
    assert walk_pairs == graph_pairs
    # final length consistent
    length = sum(math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in zip(result.points, result.points[1:]))
    assert close(length, result.stats["final_length"], 1e-6 * max(1.0, length))
    return True


# ---------------------------------------------------------------------------

def test_single_closed_loop():
    r = run_pipeline([square(0, 0, 10)], snap_tol=0.1)
    s = r.stats
    assert s["components"] == 1
    assert s["bridges"] == 0
    assert s["retrace_length"] == 0
    assert s["overhead_pct"] == 0
    assert close(s["final_length"], 40)
    assert len(r.points) == 5
    assert r.points[0] == r.points[-1]
    assert r.points[0] == (0, 10)          # bottom-left in SVG coordinates (max y)
    check_walk(r)


def test_two_disjoint_loops():
    r = run_pipeline([square(0, 0, 10), square(20, 0, 10)], snap_tol=0.1)
    s = r.stats
    assert s["components"] == 2
    assert s["bridges"] == 1
    assert close(s["bridge_length"], 10)   # nearest points: (10,y) -> (20,y)
    assert close(s["retrace_length"], 10)  # bridge must be walked back
    assert close(s["final_length"], 100)
    assert close(s["overhead_pct"], 20)
    assert not s["open_path"]
    check_walk(r)

    # open path: the bridge is only walked once
    r2 = run_pipeline([square(0, 0, 10), square(20, 0, 10)], snap_tol=0.1, allow_open_path=True)
    assert r2.stats["bridges"] == 1
    assert close(r2.stats["retrace_length"], 0)
    assert close(r2.stats["final_length"], 90)
    assert r2.stats["open_path"]
    assert r2.points[0] != r2.points[-1]
    check_walk(r2)


def test_figure_eight():
    # two squares sharing the corner (10,10): one component, all even
    r = run_pipeline([square(0, 0, 10), square(10, 10, 10)], snap_tol=0.1)
    s = r.stats
    assert s["components"] == 1
    assert s["bridges"] == 0
    assert s["odd_nodes"] == 0
    assert s["retrace_length"] == 0
    assert close(s["final_length"], 80)
    check_walk(r)


def test_tree_all_odd():
    # "Y" tree: centre has degree 3, three leaves degree 1 -> all 4 nodes odd
    tree = [[(0, 0), (0, -10)], [(0, 0), (-8, 6)], [(0, 0), (8, 6)]]
    r = run_pipeline(tree, snap_tol=0.01)
    s = r.stats
    assert s["odd_nodes"] == 4
    assert s["components"] == 1
    drawn = 3 * 10
    # every perfect matching of {centre, 3 leaves} is one leaf-leaf pair via
    # the centre (20) plus centre-leaf (10) = 30: all three arms are retraced
    assert close(s["retrace_length"], 30)
    assert close(s["final_length"], drawn + 30)
    check_walk(r)

    # open path: start at an odd node, one pair unmatched -> at most 20 retraced
    r2 = run_pipeline(tree, snap_tol=0.01, allow_open_path=True)
    assert r2.stats["open_path"]
    assert r2.stats["retrace_length"] <= 20 + EPS
    assert r2.stats["retrace_length"] >= 10 - EPS
    check_walk(r2)


def test_star_tree():
    star = [[(0, 0), (1, 0)], [(0, 0), (-1, 0)], [(0, 0), (0, 1)], [(0, 0), (0, -1)]]
    r = run_pipeline(star, snap_tol=0.01)
    assert r.stats["odd_nodes"] == 4
    assert close(r.stats["retrace_length"], 4)   # two leaf pairs via the centre
    assert close(r.stats["final_length"], 8)
    check_walk(r)


def test_grid():
    # 3x3 unit squares (4x4 nodes) drawn as 8 polylines WITH a vertex at
    # every crossing (segments that merely cross are not connected -- the
    # extension joins vertices, it does not split segments)
    lines = []
    for i in range(4):
        lines.append([(i, j) for j in range(4)])
        lines.append([(j, i) for j in range(4)])
    r = run_pipeline(lines, snap_tol=0.01)
    s = r.stats
    assert s["edges"] == 24
    assert s["components"] == 1
    assert s["odd_nodes"] == 8                 # the non-corner border nodes
    assert close(s["retrace_length"], 4)       # optimum: pair adjacent border nodes
    assert close(s["final_length"], 28)
    check_walk(r)


def test_nested_contours():
    r = run_pipeline([square(0, 0, 10), square(2.5, 2.5, 5)], snap_tol=0.1)
    s = r.stats
    assert s["components"] == 2
    assert s["bridges"] == 1
    assert close(s["bridge_length"], math.hypot(2.5, 2.5))   # corner to corner
    assert close(s["retrace_length"], s["bridge_length"])
    check_walk(r)


def test_snapping_joins_adjacent_contours():
    # two squares whose shared edge is 0.05 apart: with tol 0.1 they share nodes
    pls = [square(0, 0, 10), square(10.05, 0, 10)]
    r = run_pipeline(pls, snap_tol=0.1)
    assert r.stats["components"] == 1
    assert r.stats["bridges"] == 0
    assert r.stats["nodes"] == 6
    check_walk(r)
    # with a tighter tolerance they stay apart
    r2 = run_pipeline(pls, snap_tol=0.01)
    assert r2.stats["components"] == 2
    assert r2.stats["bridges"] == 1
    assert close(r2.stats["bridge_length"], 0.05)


def test_degenerate_inputs():
    pls = [
        [(5, 5)],                              # single point
        [(1, 1), (1, 1), (1, 1)],              # zero length only
        [],                                    # empty
        [(0, 0), (10, 0), (10, 0), (10, 10)],  # duplicate vertex
        [(0, 0), (10, 0)],                     # duplicate of an existing segment
        [(10, 0), (0, 0)],                     # same, reversed
    ]
    r = run_pipeline(pls, snap_tol=0.01)
    s = r.stats
    assert s["dropped_degenerate"] == 3
    assert s["zero_length_dropped"] >= 1
    assert s["duplicates_removed"] == 2
    assert s["edges"] == 2
    check_walk(r)

    # keep multiplicity
    r2 = run_pipeline(pls, snap_tol=0.01, dedupe=False)
    assert r2.stats["edges"] == 4
    assert r2.stats["duplicates_removed"] == 0
    check_walk(r2)

    # nothing usable at all
    r3 = run_pipeline([[(1, 1)], []], snap_tol=0.1)
    assert r3.points == []
    assert r3.stats["edges"] == 0
    assert "nothing to do" in format_report(r3.stats)
    r4 = run_pipeline([], snap_tol=0.1)
    assert r4.points == []


def test_min_island_filter():
    pls = [square(0, 0, 10), square(50, 50, 0.3), square(60, 60, 0.3), [(80, 0), (80, 0.2)]]
    kept, degenerate, islands = filter_polylines(pls, min_island=1.0, closed_tol=0.01)
    assert islands == 2
    assert degenerate == 0
    assert len(kept) == 2                      # the open 0.2 segment is not an island
    r = run_pipeline(pls, snap_tol=0.01, min_island=1.0)
    assert r.stats["dropped_islands"] == 2
    assert r.stats["components"] == 2


def test_start_corners():
    pls = [square(0, 0, 10)]
    expect = {"bottom-left": (0, 10), "top-left": (0, 0), "top-right": (10, 0), "bottom-right": (10, 10)}
    for corner, pt in expect.items():
        r = run_pipeline(pls, snap_tol=0.1, start_corner=corner)
        assert r.points[0] == pt, corner


def test_bridge_style_hook():
    try:
        run_pipeline([square(0, 0, 1)], bridge_style="route")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown bridge style must raise")
    g, _ = build_graph([square(0, 0, 1), square(5, 0, 1)], 0.01)
    comp, n = connected_components(g)
    try:
        bridge_components(g, comp, n, bridge_style="route")
    except NotImplementedError:
        pass
    else:
        raise AssertionError("NotImplementedError expected")


def test_euler_circuit_direct():
    g = Graph()
    for p in [(0, 0), (1, 0), (1, 1), (0, 1), (2, 0)]:
        g.add_node(*p)
    for u, v in [(0, 1), (1, 2), (2, 3), (3, 0), (1, 4), (4, 2)]:
        g.add_edge(u, v)
    assert g.odd_nodes() == [1, 2]
    seq = euler_circuit(g, 1)          # Euler path from one odd node to the other
    assert len(seq) == 7
    assert seq[0] == 1 and seq[-1] == 2
    new, start, end = eulerize(g, start_hint=(0, 0))
    assert g.odd_nodes() == []
    assert start == end == 0
    seq = euler_circuit(g, 0)
    assert seq[0] == seq[-1] == 0 and len(seq) == g.num_edges() + 1


def test_spatial_hash():
    h = SpatialHash(1.0)
    h.insert(0.5, 0.5, "a")
    h.insert(1.5, 0.5, "b")
    h.insert(5.5, 5.5, "c")
    near = set(h.neighbourhood(0.9, 0.9))
    assert near == {"a", "b"}
    assert set(h.neighbourhood(5.2, 5.2)) == {"c"}


def test_many_components_mst_quality():
    # 10x10 grid of unit squares spaced 2 apart: MST needs 99 bridges of length 1
    pls = [square(3 * i, 3 * j, 1) for i in range(10) for j in range(10)]
    r = run_pipeline(pls, snap_tol=0.01)
    s = r.stats
    assert s["components"] == 100
    assert s["bridges"] == 99
    assert close(s["bridge_length"], 99 * 2.0)       # gap between squares is 2
    check_walk(r)


def test_performance_medium():
    random.seed(3)
    pls = []
    for _ in range(800):
        cx, cy = random.uniform(0, 800), random.uniform(0, 400)
        rr = random.uniform(2, 15)
        n = 25
        pts = [(cx + rr * math.cos(2 * math.pi * k / n), cy + rr * math.sin(2 * math.pi * k / n))
               for k in range(n)]
        pls.append(pts + [pts[0]])
    t = time.perf_counter()
    r = run_pipeline(pls, snap_tol=0.1)
    dt = time.perf_counter() - t
    assert r.stats["input_segments"] == 20000
    assert dt < 10.0, "20k segments took %.1fs" % dt
    check_walk(r)


# ---------------------------------------------------------------------------
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
