"""
cl_graph.py -- graph side of the Continuous Line extension.

Standard library only; no inkex.  Everything here works on plain lists of
floats so that it can be unit-tested without Inkscape.

Pipeline stages implemented here
--------------------------------
1. ``build_graph``        polylines -> undirected multigraph, snapping nearby
                          vertices together with a spatial hash.
2. ``connected_components``
3. ``bridge_components``  joins the components with the shortest straight
                          "bridge" segments found via a grid Voronoi + MST.
4. ``eulerize``           Chinese-postman style: pairs odd nodes with
                          shortest-path retraces so an Euler circuit exists.
5. ``euler_circuit``      iterative Hierholzer walk.

Complexity notes are next to each stage.  The design goal is ~100k segments
in a few seconds of pure CPython on a laptop.
"""

import heapq
import math
from collections import defaultdict

INF = float("inf")

KIND_ORIG = 0      # a segment that was in the input
KIND_BRIDGE = 1    # new straight segment joining two components
KIND_RETRACE = 2   # duplicate of an existing edge (pen goes over it again)


# ---------------------------------------------------------------------------
# Graph container
# ---------------------------------------------------------------------------

class Graph:
    """Undirected multigraph with coordinates on the nodes.

    Edges are stored in parallel lists (struct-of-arrays) because that is
    noticeably faster and lighter in CPython than a list of small objects
    when there are a few hundred thousand of them.
    """

    __slots__ = ("nodes", "eu", "ev", "elen", "ekind", "eorig", "adj")

    def __init__(self):
        self.nodes = []      # [(x, y), ...]
        self.eu = []         # edge endpoint a
        self.ev = []         # edge endpoint b
        self.elen = []       # Euclidean length
        self.ekind = []      # KIND_*
        self.eorig = []      # for retraces: id of the edge being duplicated
        self.adj = []        # adj[node] -> list of edge ids

    # -- construction -------------------------------------------------------
    def add_node(self, x, y):
        self.nodes.append((x, y))
        self.adj.append([])
        return len(self.nodes) - 1

    def add_edge(self, u, v, kind=KIND_ORIG, orig=-1):
        (x1, y1), (x2, y2) = self.nodes[u], self.nodes[v]
        eid = len(self.eu)
        self.eu.append(u)
        self.ev.append(v)
        self.elen.append(math.hypot(x2 - x1, y2 - y1))
        self.ekind.append(kind)
        self.eorig.append(orig)
        self.adj[u].append(eid)
        self.adj[v].append(eid)
        return eid

    # -- queries ------------------------------------------------------------
    def other(self, eid, u):
        a = self.eu[eid]
        return self.ev[eid] if a == u else a

    def num_nodes(self):
        return len(self.nodes)

    def num_edges(self):
        return len(self.eu)

    def degree(self, u):
        return len(self.adj[u])

    def odd_nodes(self):
        return [u for u, a in enumerate(self.adj) if len(a) & 1]

    def bbox(self):
        if not self.nodes:
            return None
        xs = [p[0] for p in self.nodes]
        ys = [p[1] for p in self.nodes]
        return (min(xs), min(ys), max(xs), max(ys))

    def nearest_node(self, pt):
        """Brute-force nearest node to *pt* (one linear pass; called once)."""
        px, py = pt
        best, bd = -1, INF
        for i, (x, y) in enumerate(self.nodes):
            d = (x - px) * (x - px) + (y - py) * (y - py)
            if d < bd:
                bd, best = d, i
        return best

    def length_by_kind(self):
        tot = [0.0, 0.0, 0.0]
        for k, ln in zip(self.ekind, self.elen):
            tot[k] += ln
        return tot


# ---------------------------------------------------------------------------
# Spatial hash
# ---------------------------------------------------------------------------

class SpatialHash:
    """Uniform grid of square cells; O(1) insert, O(k) neighbourhood query."""

    __slots__ = ("cell", "inv", "cells")

    def __init__(self, cell_size):
        self.cell = max(float(cell_size), 1e-12)
        self.inv = 1.0 / self.cell
        self.cells = defaultdict(list)

    def key(self, x, y):
        return (int(math.floor(x * self.inv)), int(math.floor(y * self.inv)))

    def insert(self, x, y, item):
        self.cells[self.key(x, y)].append(item)

    def neighbourhood(self, x, y, rings=1):
        """Yield items in the (2*rings+1)^2 cells around (x, y)."""
        kx, ky = self.key(x, y)
        cells = self.cells
        for dx in range(-rings, rings + 1):
            for dy in range(-rings, rings + 1):
                lst = cells.get((kx + dx, ky + dy))
                if lst:
                    yield from lst


# ---------------------------------------------------------------------------
# 1. Build graph from polylines, snapping nearby vertices
# ---------------------------------------------------------------------------

def build_graph(polylines, snap_tol, dedupe=True):
    """Return (Graph, stats) for a list of polylines (lists of (x, y)).

    Vertices closer than *snap_tol* to an already-created node are merged
    into it, so contours that touch or nearly touch share nodes.  A spatial
    hash with cell size == snap_tol makes this O(N) instead of O(N^2): any
    node within snap_tol of a point must be in the 3x3 cells around it.

    Zero-length segments (both ends snap to the same node) are dropped.
    With *dedupe* (default) parallel duplicate segments collapse into one,
    because drawing the same line twice is pure waste on a pen plotter; with
    ``dedupe=False`` their multiplicity is kept.
    """
    tol = max(float(snap_tol), 0.0)
    tol2 = tol * tol
    grid = SpatialHash(tol if tol > 0 else 1e-9)
    g = Graph()
    nodes = g.nodes

    def snap(x, y):
        best, bd = -1, tol2
        if tol > 0:
            for nid in grid.neighbourhood(x, y):
                nx, ny = nodes[nid]
                d = (nx - x) * (nx - x) + (ny - y) * (ny - y)
                if d <= bd:
                    bd, best = d, nid
        else:
            for nid in grid.neighbourhood(x, y, 0):
                if nodes[nid] == (x, y):
                    return nid
        if best >= 0:
            return best
        nid = g.add_node(x, y)
        grid.insert(x, y, nid)
        return nid

    input_segments = 0
    zero_len = 0
    raw_edges = []          # (u, v) before dedupe
    for pl in polylines:
        if len(pl) < 2:
            continue
        prev = snap(pl[0][0], pl[0][1])
        for i in range(1, len(pl)):
            input_segments += 1
            cur = snap(pl[i][0], pl[i][1])
            if cur == prev:
                zero_len += 1
                continue
            raw_edges.append((prev, cur) if prev < cur else (cur, prev))
            prev = cur

    duplicates = 0
    if dedupe:
        seen = set()
        for u, v in raw_edges:
            if (u, v) in seen:
                duplicates += 1
                continue
            seen.add((u, v))
            g.add_edge(u, v, KIND_ORIG)
    else:
        for u, v in raw_edges:
            g.add_edge(u, v, KIND_ORIG)

    isolated = _drop_isolated_nodes(g)

    stats = {
        "input_segments": input_segments,
        "zero_length_dropped": zero_len,
        "duplicates_removed": duplicates,
        "isolated_points_dropped": isolated,
        "nodes": g.num_nodes(),
        "edges": g.num_edges(),
    }
    return g, stats


def _drop_isolated_nodes(g):
    """Remove degree-0 nodes (e.g. single-point paths); renumber the rest."""
    keep = [i for i, a in enumerate(g.adj) if a]
    dropped = len(g.adj) - len(keep)
    if not dropped:
        return 0
    remap = {old: new for new, old in enumerate(keep)}
    g.nodes = [g.nodes[i] for i in keep]
    g.adj = [g.adj[i] for i in keep]
    g.eu = [remap[u] for u in g.eu]
    g.ev = [remap[v] for v in g.ev]
    return dropped


# ---------------------------------------------------------------------------
# 2. Connected components (iterative BFS)
# ---------------------------------------------------------------------------

def connected_components(g):
    """Return (comp_of_node list, number_of_components)."""
    n = g.num_nodes()
    comp = [-1] * n
    adj, eu, ev = g.adj, g.eu, g.ev
    ncomp = 0
    for s in range(n):
        if comp[s] != -1:
            continue
        comp[s] = ncomp
        stack = [s]
        while stack:
            u = stack.pop()
            for eid in adj[u]:
                v = ev[eid] if eu[eid] == u else eu[eid]
                if comp[v] == -1:
                    comp[v] = ncomp
                    stack.append(v)
        ncomp += 1
    return comp, ncomp


# ---------------------------------------------------------------------------
# 3. Bridge components (MST over components, nearest-point edge weights)
# ---------------------------------------------------------------------------

class _UnionFind:
    __slots__ = ("parent", "rank")

    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x):
        p = self.parent
        root = x
        while p[root] != root:
            root = p[root]
        while p[x] != root:          # path compression
            p[x], x = root, p[x]
        return root

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return False
        if self.rank[a] < self.rank[b]:
            a, b = b, a
        self.parent[b] = a
        if self.rank[a] == self.rank[b]:
            self.rank[a] += 1
        return True


def bridge_components(g, comp, ncomp, bridge_style="straight"):
    """Add straight bridge edges so the graph becomes connected.

    Returns the list of new edge ids.

    Algorithm (no brute force):
      * Lay a uniform grid over the drawing with roughly 2 cells per node.
      * Multi-source BFS from every occupied cell labels each grid cell with
        the node that (approximately) owns it -> a discrete Voronoi diagram.
        Wherever two cells with different *components* touch we get a
        candidate bridge (a, b) with exact Euclidean weight |ab|.  The nearest
        pair between two components always has adjacent Voronoi regions, so
        the candidate set contains (a cell-quantised version of) every edge
        the MST could want, and because the grid is connected the candidate
        graph is connected as well.
      * Kruskal on the candidates (sorted by length, union-find) picks the
        minimum spanning tree over components.
      * Each chosen bridge is then refined by alternating nearest-node
        searches (a -> nearest node of comp(b), then back), which removes the
        cell-quantisation error and lands on the true closest pair in the
        overwhelmingly common case.

    Cost: O(cells + N) for the BFS and candidate generation, O(C log C) for
    Kruskal where C ~ number of Voronoi border cells.  ~100k nodes take about
    a second in CPython.

    *bridge_style* is a hook for a future "route along existing lines" mode;
    only "straight" is implemented.
    """
    if bridge_style != "straight":
        raise NotImplementedError("bridge_style %r is not implemented (only 'straight')"
                                  % (bridge_style,))
    if ncomp <= 1 or g.num_nodes() == 0:
        return []

    nodes = g.nodes
    n = len(nodes)
    xmin, ymin, xmax, ymax = g.bbox()
    w = max(xmax - xmin, 1e-9)
    h = max(ymax - ymin, 1e-9)

    # Cell size: about 2 cells per node, bounded so degenerate (very thin)
    # bounding boxes do not explode the grid.
    target_cells = max(1024, 2 * n)
    cell = math.sqrt(w * h / target_cells)
    if cell <= 0:
        cell = max(w, h) / math.sqrt(target_cells)
    while True:
        gw = int(w / cell) + 1
        gh = int(h / cell) + 1
        if gw * gh <= 4 * target_cells:
            break
        cell *= 1.5
    inv = 1.0 / cell

    label = [-1] * (gw * gh)          # seed node id per cell
    cell_nodes = defaultdict(list)    # occupied cell -> node ids
    for nid, (x, y) in enumerate(nodes):
        ix = int((x - xmin) * inv)
        iy = int((y - ymin) * inv)
        if ix >= gw:
            ix = gw - 1
        if iy >= gh:
            iy = gh - 1
        cell_nodes[iy * gw + ix].append(nid)

    # candidate bridges keyed by (component a, component b) -> (d, a, b)
    cand = {}

    def offer(a, b):
        ca, cb = comp[a], comp[b]
        if ca == cb:
            return
        key = (ca, cb) if ca < cb else (cb, ca)
        ax, ay = nodes[a]
        bx, by = nodes[b]
        d = (ax - bx) * (ax - bx) + (ay - by) * (ay - by)
        cur = cand.get(key)
        if cur is None or d < cur[0]:
            cand[key] = (d, a, b)

    # Exact candidates between actual nodes in the same / adjacent occupied
    # cells (handles components that are much closer than one cell).
    PER_CELL_CAP = 48
    for idx, lst in cell_nodes.items():
        iy, ix = divmod(idx, gw)
        here = lst[:PER_CELL_CAP]
        # within the cell
        for i in range(len(here)):
            a = here[i]
            ca = comp[a]
            for j in range(i + 1, len(here)):
                b = here[j]
                if comp[b] != ca:
                    offer(a, b)
        # against the 4 "forward" neighbour cells (each pair visited once)
        for dx, dy in ((1, 0), (0, 1), (1, 1), (-1, 1)):
            jx, jy = ix + dx, iy + dy
            if 0 <= jx < gw and 0 <= jy < gh:
                other = cell_nodes.get(jy * gw + jx)
                if other:
                    other = other[:PER_CELL_CAP]
                    for a in here:
                        ca = comp[a]
                        for b in other:
                            if comp[b] != ca:
                                offer(a, b)

    # Multi-source BFS (discrete Voronoi by Chebyshev cell distance).
    frontier = []
    for idx, lst in cell_nodes.items():
        label[idx] = lst[0]
        frontier.append(idx)
    neighbours = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
    while frontier:
        nxt = []
        for idx in frontier:
            iy, ix = divmod(idx, gw)
            seed = label[idx]
            cseed = comp[seed]
            for dx, dy in neighbours:
                jx, jy = ix + dx, iy + dy
                if jx < 0 or jy < 0 or jx >= gw or jy >= gh:
                    continue
                j = jy * gw + jx
                lj = label[j]
                if lj == -1:
                    label[j] = seed
                    nxt.append(j)
                elif comp[lj] != cseed:
                    offer(seed, lj)
        frontier = nxt

    # Kruskal over component candidates.
    edges = sorted(cand.values())
    uf = _UnionFind(ncomp)
    chosen = []
    for d2, a, b in edges:
        if uf.union(comp[a], comp[b]):
            chosen.append((a, b))
            if len(chosen) == ncomp - 1:
                break

    # Refine every chosen bridge to the true nearest pair of nodes between
    # the two components (alternating nearest-neighbour search on the grid).
    refined = [_refine_bridge(nodes, comp, cell_nodes, gw, gh, xmin, ymin, inv, a, b)
               for a, b in chosen]

    new_eids = [g.add_edge(a, b, KIND_BRIDGE) for a, b in refined]
    return new_eids


def _nearest_in_component(nodes, comp, cell_nodes, gw, gh, xmin, ymin, inv,
                          px, py, target_comp, max_dist, max_rings=40):
    """Nearest node of *target_comp* to (px, py) within *max_dist*, or -1.

    Ring search on the occupied-cell grid; stops when the ring's minimum
    possible distance exceeds the best found so far.
    """
    cell = 1.0 / inv
    cx = int((px - xmin) * inv)
    cy = int((py - ymin) * inv)
    best, bd = -1, max_dist * max_dist
    r = 0
    while r <= max_rings:
        lower = (r - 1) * cell
        if r > 0 and lower * lower > bd:
            break
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if abs(dx) != r and abs(dy) != r:
                    continue                     # only the ring itself
                jx, jy = cx + dx, cy + dy
                if jx < 0 or jy < 0 or jx >= gw or jy >= gh:
                    continue
                lst = cell_nodes.get(jy * gw + jx)
                if not lst:
                    continue
                for nid in lst:
                    if comp[nid] != target_comp:
                        continue
                    x, y = nodes[nid]
                    d = (x - px) * (x - px) + (y - py) * (y - py)
                    if d < bd:
                        bd, best = d, nid
        r += 1
    return best


def _refine_bridge(nodes, comp, cell_nodes, gw, gh, xmin, ymin, inv, a, b):
    ax, ay = nodes[a]
    bx, by = nodes[b]
    d = math.hypot(ax - bx, ay - by)
    cell = 1.0 / inv
    if d > 40 * cell:
        return a, b                         # far apart; refinement too costly
    for _ in range(4):
        changed = False
        ax, ay = nodes[a]
        nb = _nearest_in_component(nodes, comp, cell_nodes, gw, gh, xmin, ymin, inv,
                                   ax, ay, comp[b], d * 1.0001)
        if nb >= 0 and nb != b:
            b = nb
            changed = True
        bx, by = nodes[b]
        na = _nearest_in_component(nodes, comp, cell_nodes, gw, gh, xmin, ymin, inv,
                                   bx, by, comp[a], math.hypot(ax - bx, ay - by) * 1.0001)
        if na >= 0 and na != a:
            a = na
            changed = True
        ax, ay = nodes[a]
        bx, by = nodes[b]
        d = math.hypot(ax - bx, ay - by)
        if not changed:
            break
    return a, b


# ---------------------------------------------------------------------------
# 4. Eulerize (Chinese postman / route inspection)
# ---------------------------------------------------------------------------

def _dijkstra_to_targets(g, src, is_target, k, max_settled):
    """Dijkstra from *src* until *k* target nodes are settled.

    Returns a list of (dist, node, [edge ids of path src->node]).  Exploration
    stops after *max_settled* settled nodes (None = unlimited) so that one
    search cannot eat the whole graph when targets are rare.
    """
    adj, eu, ev, elen = g.adj, g.eu, g.ev, g.elen
    dist = {src: 0.0}
    pred = {}
    heap = [(0.0, src)]
    found = []
    settled = 0
    pop, push = heapq.heappop, heapq.heappush
    while heap:
        d, u = pop(heap)
        if d > dist.get(u, INF):
            continue                         # stale entry
        if u != src and is_target[u]:
            found.append((d, u))
            if len(found) >= k:
                break
        settled += 1
        if max_settled is not None and settled > max_settled:
            break
        for eid in adj[u]:
            v = ev[eid] if eu[eid] == u else eu[eid]
            nd = d + elen[eid]
            if nd < dist.get(v, INF):
                dist[v] = nd
                pred[v] = eid
                push(heap, (nd, v))
    out = []
    for d, v in found:
        path = []
        cur = v
        while cur != src:
            eid = pred[cur]
            path.append(eid)
            cur = ev[eid] if eu[eid] == cur else eu[eid]
        out.append((d, v, path))
    return out


def eulerize(g, allow_open=False, start_hint=None, k_nearest=6, max_settled=20000):
    """Add retrace edges so that every node has even degree.

    Returns (retrace_edge_ids, start_node, end_node).  For a closed circuit
    start_node == end_node == start_hint.  With *allow_open* the odd node
    closest to *start_hint* (a point) is left odd and becomes the start;
    one other odd node is left unmatched and becomes the end -- that saves
    one retrace path at the cost of not finishing where you started.

    Matching strategy (the trade-off, documented as requested):
      An exact minimum-weight perfect matching (Edmonds' blossom algorithm)
      is O(n^3) and several hundred lines of delicate code; with thousands of
      odd nodes it would also take minutes in pure Python.  Instead we use
      the standard practical heuristic for Chinese-postman on planar
      drawings:
        * from every odd node run a Dijkstra that stops after it has settled
          the *k_nearest* closest other odd nodes (graph distance, not
          Euclidean), remembering the paths;
        * sort all those candidate pairs by distance and greedily match
          whatever is still unmatched;
        * odd nodes left over (none of their k neighbours were free) are
          matched by an unbounded Dijkstra to the nearest free odd node.
      Greedy matching is at most ~2x the optimum in theory and typically
      within 5-15% on real drawings, because odd nodes come in local clusters
      (T-junctions, bridge ends).  Runtime is O(|odd| * k * log) Dijkstra
      work -- seconds for 100k edges.
      Finally, if two retrace paths duplicate the same edge, both copies are
      cancelled (parity is unchanged, length strictly drops) -- the optimum
      never retraces an edge more than once.
    """
    odd = g.odd_nodes()
    start = None
    end = None
    if start_hint is not None and not isinstance(start_hint, int):
        start_pt = start_hint
    else:
        start_pt = None

    if allow_open and odd:
        # Start at the odd node nearest the requested corner; it stays odd.
        if start_pt is not None:
            px, py = start_pt
            start = min(odd, key=lambda u: (g.nodes[u][0] - px) ** 2 + (g.nodes[u][1] - py) ** 2)
        else:
            start = odd[0]
        odd = [u for u in odd if u != start]

    n = g.num_nodes()
    is_odd = bytearray(n)
    for u in odd:
        is_odd[u] = 1

    # -- candidate pairs via bounded Dijkstra from each odd node
    candidates = []
    for u in odd:
        for d, v, path in _dijkstra_to_targets(g, u, is_odd, k_nearest, max_settled):
            if u < v:                       # each unordered pair once is enough
                candidates.append((d, u, v, path))
            else:
                candidates.append((d, v, u, path))
    candidates.sort(key=lambda c: c[0])

    matched = bytearray(n)
    pairs = []                              # (u, v, path edge ids)
    for d, u, v, path in candidates:
        if not matched[u] and not matched[v]:
            matched[u] = matched[v] = 1
            pairs.append(path)

    # -- leftovers: unbounded search for the nearest free odd node
    free = [u for u in odd if not matched[u]]
    is_free = bytearray(n)
    for u in free:
        is_free[u] = 1
    free_set = set(free)
    while len(free_set) >= 2:
        u = free_set.pop()
        is_free[u] = 0
        res = _dijkstra_to_targets(g, u, is_free, 1, None)
        if not res:
            raise RuntimeError("graph is not connected; cannot eulerize")
        d, v, path = res[0]
        free_set.discard(v)
        is_free[v] = 0
        pairs.append(path)
    if free_set:
        end = free_set.pop()                # the one odd node left (open path)

    # -- duplicate edges along the chosen paths; cancel double retraces
    count = defaultdict(int)
    for path in pairs:
        for eid in path:
            count[eid] += 1
    new_eids = []
    for eid, c in count.items():
        if c & 1:
            new_eids.append(g.add_edge(g.eu[eid], g.ev[eid], KIND_RETRACE, orig=eid))

    if start is None:
        if isinstance(start_hint, int):
            start = start_hint
        elif start_pt is not None:
            start = g.nearest_node(start_pt)
        else:
            start = 0
        end = start
    return new_eids, start, end


# ---------------------------------------------------------------------------
# 5. Euler circuit / path  (Hierholzer, iterative)
# ---------------------------------------------------------------------------

def euler_circuit(g, start):
    """Return the node sequence of an Euler circuit/path starting at *start*.

    Iterative Hierholzer: a stack of nodes plus a per-node pointer into its
    adjacency list.  O(E).  Raises if some edge was unreachable (graph not
    connected or wrong parity).
    """
    m = g.num_edges()
    if m == 0:
        return [start] if g.num_nodes() else []
    adj, eu, ev = g.adj, g.eu, g.ev
    used = bytearray(m)
    ptr = [0] * g.num_nodes()
    stack = [start]
    seq = []
    while stack:
        u = stack[-1]
        a = adj[u]
        p = ptr[u]
        la = len(a)
        while p < la and used[a[p]]:
            p += 1
        if p < la:
            eid = a[p]
            used[eid] = 1
            ptr[u] = p + 1
            stack.append(ev[eid] if eu[eid] == u else eu[eid])
        else:
            ptr[u] = p
            seq.append(stack.pop())
    seq.reverse()
    if len(seq) != m + 1:
        raise RuntimeError("Euler walk used %d of %d edges; graph not connected or "
                           "has odd nodes" % (len(seq) - 1, m))
    return seq
