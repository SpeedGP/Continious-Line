# Continuous Line — zero pen-lift path merger for Inkscape 1.4

-------------------
This is a first for me. This was coded completely with Claude Code, I am a noob with a drawing robot and no pen-lifting mechanism.
-------------------

Turns everything in your drawing into **one `<path>` with a single subpath**
(one `M`, then only `L` segments, no `Z`), so a G-code generator that has no
pen-lift — the J Tech Photonics Laser Tool on a plotter whose pen is always
down — produces no rapid moves after the first one.

* Disconnected shapes are joined with the **shortest straight "bridge"
  lines** (minimum spanning tree over the shapes, nearest point to nearest
  point).
* Where a line has to be drawn twice (route-inspection / Chinese postman),
  the duplicates are chosen to **minimise the total retraced length**.
* Pure Python standard library. No numpy, no pip packages. Works with the
  Python bundled with Inkscape on Windows.

```
continuous_line/
  continuous_line.inx   dialog: Extensions > Generate from Path > Continuous Line
  continuous_line.py    Inkscape entry point (inkex.EffectExtension)
  cl_pipeline.py        the core: run_pipeline(polylines, ...) -> one polyline + stats
  cl_geometry.py        bezier flattening, transforms, bbox, units
  cl_graph.py           spatial hash, graph, components, MST bridging, eulerize, Hierholzer
  cl_cli.py             command-line runner (no Inkscape needed) for batch testing
  tests/                pytest tests + sample SVGs
```

## Install (Windows, Inkscape 1.4.x)

1. Press `Win+R`, type `%APPDATA%\inkscape\extensions` and press Enter
   (create the `extensions` folder if it does not exist).
2. Copy the whole `continuous_line` folder into it, so you end up with
   `%APPDATA%\inkscape\extensions\continuous_line\continuous_line.inx` and
   the `.py` files next to it. (Copying only the files, without the folder,
   also works. No symlinks or case tricks are needed.)
3. Restart Inkscape. The extension appears under
   **Extensions ▸ Generate from Path ▸ Continuous Line…**

You can check where Inkscape looks for user extensions under
*Edit ▸ Preferences ▸ System ▸ User extensions*.

macOS / Linux: `~/Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions/`
and `~/.config/inkscape/extensions/` respectively.

## Workflow with the J Tech Photonics Laser Tool

1. Open / import your SVG (clip art, Trace Bitmap result, …).
2. If the drawing contains **text**, select it and run *Path ▸ Object to
   Path* (`Shift+Ctrl+C`). If it contains **clones**, select them and run
   *Edit ▸ Clone ▸ Unlink Clone* (`Shift+Alt+D`). The extension warns you
   about anything it had to skip.
3. Select the objects to plot — or select nothing to process the whole
   document.
4. **Extensions ▸ Generate from Path ▸ Continuous Line…**, set the options
   (defaults are fine for most drawings), *Apply*.
   The originals are replaced by one path called `continuous-line` on the
   current layer, drawn as a thin black stroke, and a report pops up.
5. Read the report (below). If the overhead is too high, undo and adjust
   (e.g. raise the minimum island size, enable *Allow open path*).
6. Select the new path and run the **J Tech Photonics Laser Tool** as you
   always do. Because there is only one subpath, the generated G-code has a
   single rapid (home → start corner) and then only `G1` moves.
7. Load in cncjs.

The drawing starts at the vertex closest to the chosen corner of its
bounding box (default bottom-left, where GRBL homes), so the one unavoidable
rapid from home is as short as it can be.

## Options

| Option | Default | Meaning |
|---|---|---|
| **Curve flatten tolerance (mm)** | 0.1 | Curves are converted to straight segments so that the chord never deviates more than this from the true curve. Smaller = smoother, more segments. 0.05–0.2 mm is sensible for a pen. |
| **Merge / snap tolerance (mm)** | 0.1 | Vertices closer together than this become one point. Traced shapes that touch, or shapes whose corners are nearly coincident, are then connected without a bridge. Too large a value will weld things you didn't intend. |
| **Start corner** | Bottom-left | Corner of the drawing's bounding box the line starts from (and, for a closed circuit, returns to). "Bottom" means the bottom of the page as you see it in Inkscape. |
| **Minimum island size (mm)** | 0 | Closed contours whose bounding box is smaller than this in *both* directions are discarded before anything else. Use it to kill trace noise specks that would otherwise each cost a bridge. 0 keeps everything. |
| **Bridge style** | Straight line | How separate shapes are joined. Only *Straight line* exists today; the dropdown is the hook for a future "route along existing lines" mode. |
| **Allow open path (end anywhere)** | off | Normally the result is a closed circuit that ends where it started. With this on, the line may end somewhere else (an Eulerian *path*), which saves one retrace. The start is then the odd-degree vertex nearest the chosen corner, which may be a little further from the corner than the closest vertex. |
| **Keep original** | off | Instead of deleting the source objects, move them to a new hidden layer named *Original*. |
| **Output stroke width (mm)** | 0.3 | Purely cosmetic; J Tech ignores it. |

Units: all option values are millimetres, regardless of the document's
user unit (mm, px, in with a viewBox scale …). The conversion uses the
document's viewBox scale, the same way Inkscape itself does.

## Reading the report

```
Continuous Line report
  input segments:        598  (20 polylines; 0 zero-length, 0 duplicate, 0 single-point dropped)
  graph:                 598 nodes, 598 edges, 38 odd nodes
  components found:      20
  bridge lines added:    19, total 141.8 mm
  retraced:              19 edges, total 141.8 mm
  drawing length:        776.7 mm
  final path length:     1060.2 mm  (637 points, closed circuit)
  overhead:              283.6 mm = 26.7% of the final path
  time:                  0.01 s
  scope:                 whole document
  start point:           (5.0, 115.0) mm from top-left
```

* **input segments** — straight segments after flattening curves. Zero-length
  segments, exact duplicates and single-point paths are removed and counted.
* **graph** — distinct vertices after snapping, distinct segments, and the
  number of vertices with an odd number of lines meeting (ends of open
  strokes, T-junctions). Every odd vertex forces some retracing.
* **components found** — how many separate, unconnected pieces the drawing
  had. Each one beyond the first needs a bridge.
* **bridge lines added** — new straight lines drawn on the paper to connect
  the pieces, and their total length. In a closed circuit every bridge is
  also walked back, so expect *retraced ≈ bridges* for drawings made only of
  closed contours.
* **retraced** — existing lines that are drawn a second time, and their
  total length.
* **drawing length** — length of the lines you actually wanted.
* **final path length** — what the pen will travel: drawing + bridges + retrace.
* **overhead** — bridges + retrace as a percentage of the final path. This is
  the number to judge a drawing by. Typical values: 5–15 % for dense traced
  bitmaps, 10–30 % for scattered clip art, higher for drawings made of many
  open strokes (each open stroke has two odd ends).

Ways to reduce overhead: increase *Minimum island size* (fewer specks →
fewer bridges), raise the snap tolerance slightly so touching shapes
connect, turn on *Allow open path*, or move far-away pieces closer together
in Inkscape before running.

Limitation worth knowing: shapes are connected where they share a
**vertex** (within the snap tolerance). Two lines that merely *cross* each
other, with no node at the crossing, are not connected there and will get a
bridge/retrace instead. Traced bitmaps never have this problem (contours do
not cross); for hand-drawn line art you can add nodes at crossings with
*Path ▸ Division* / *Cut Path* first if it matters.

## Squiggle Raster — plotting photographs

A threshold trace (Trace Bitmap) renders faces as black-and-white blobs
because skin is all mid-tones. **Extensions ▸ Generate from Path ▸ Squiggle
Raster (from Image)** takes a different route: select a bitmap and it is
replaced by *one* path made of horizontal sine-wave rows whose amplitude
and frequency follow the darkness of the picture, walked back and forth
across the image. Tone comes from line density, like an engraving, and the
result is a single continuous line by construction — no bridges, no
retrace, nothing for Continuous Line to do. Feed it straight to J Tech.

| Option | Default | Meaning |
|---|---|---|
| **Rows** | 80 | Number of lines across the image. 60–120 for a portrait; detail and path length both scale with it. |
| **Line direction** | Horizontal | Rows run across (horizontal) or down (vertical) the image. |
| **Max amplitude in black** | 90 % | Peak-to-peak wiggle height where the image is black, as a percentage of the row pitch. 90 % almost fills the row; >100 % makes rows overlap. |
| **Min amplitude in white** | 0 % | 0 keeps white areas as a straight line. |
| **Max / min frequency** | 2.5 / 0.3 | Waves per row pitch in black / white. Higher max = denser, darker blacks, longer path. |
| **Tone gamma** | 1.0 | >1 lightens mid-tones (try 1.2–1.5 so faces don't go muddy); <1 darkens. |
| **Treat as white below** | 4 % | Darkness below this draws as a straight line so paper-white stays clean (hides JPEG noise). |
| **Invert** | off | For a white pen on black paper. |
| **Curve tolerance (mm)** | 0.1 | Chord error when sampling the sine. 0.2–0.3 mm is plenty for a pen and halves the point count. |
| **Feed rate for the time estimate** | 2000 | Only used for the "nominal time" line in the report. |
| **Keep image** | off | Hide the bitmap instead of deleting it. |

Workflow: import the photo, scale and place it on the page (the path fills
the image box exactly), *Extensions ▸ Images ▸ Embed Images* so the pixels
are inside the SVG, select the image, run Squiggle Raster, read the report,
then J Tech. If the photo is linked rather than embedded the extension
tries the file path next to the document; embedding avoids any doubt.

The report shows rows, points, path length and a nominal draw time. Expect
long paths: a 1 m-wide portrait at 90 rows is ~200 m of line (≈ 1.5–2 h at
F2000), because every wiggle is pen travel. Fewer rows, lower max
frequency and a gamma above 1 all shorten it. On a GRBL machine with low
acceleration the wiggles never reach full feed rate, so real time is
longer than nominal.

Preview without Inkscape: `python squiggle_cli.py photo.jpg out.svg
--width-mm 1000 --rows 90 --png preview.png` (JPEG input and the PNG
preview need Pillow; 8-bit PNG input works with the standard library).

## Command-line use (no Inkscape)

```
python cl_cli.py input.svg output.svg [--flatten-tol 0.1] [--snap-tol 0.1]
       [--start-corner bottom-left] [--min-island 0] [--open-path]
       [--png-before before.png --png-after after.png]
```

The CLI has its own small SVG reader (`<path>` with M/L/H/V/C/S/Q/T/A/Z,
`<rect>`, `<circle>`, `<ellipse>`, `<line>`, `<polyline>`, `<polygon>`,
nested `<g>` transforms). It skips `<use>`, `<text>` and `<image>` with a
warning, and ignores CSS classes — it is meant for batch testing, not as a
replacement for the Inkscape extension. The PNG options need Pillow and draw
bridges in red and retraced lines in blue.

## Using the core elsewhere

```python
from cl_pipeline import run_pipeline, format_report
result = run_pipeline(list_of_polylines, snap_tol=0.1, start_corner="bottom-left")
result.points       # [(x, y), ...]  -- one continuous polyline
result.stats        # dict; format_report(result.stats) renders the text above
```

`list_of_polylines` is a list of lists of `(x, y)` tuples in any consistent
unit. That is all a future G-code post-processor would need.

## How it works (short)

1. **Flatten** — every shape is converted to cubic Béziers via `inkex`,
   transformed to document coordinates, and flattened with Wang's formula so
   the chord error stays under the tolerance.
2. **Graph** — vertices are snapped with a spatial hash (cell = snap
   tolerance, 3×3 lookup), segments become edges. O(N).
3. **Components & bridges** — connected components by BFS. A grid Voronoi
   diagram (multi-source BFS over a grid of ~2 cells per vertex) yields
   candidate nearest pairs between components; Kruskal picks the minimum
   spanning tree; each chosen bridge is refined to the true nearest vertex
   pair by alternating nearest-neighbour searches. No O(n²) anywhere.
4. **Eulerize** — odd vertices are paired with a greedy matching over the
   *k* nearest odd vertices (graph distance via bounded Dijkstra), leftovers
   with an unbounded Dijkstra; edges along the chosen shortest paths are
   duplicated; edges duplicated twice are cancelled. Greedy instead of exact
   blossom matching keeps it to seconds on 100k edges and is typically
   within ~10 % of optimal on drawings (odd vertices cluster locally).
5. **Walk** — iterative Hierholzer, starting at the vertex nearest the chosen
   corner.

A 105 000-segment synthetic trace runs in about 3.5 s on a laptop.

## Tests

```
cd continuous_line
python -m pytest            # or, without pytest:
python tests/test_graph.py
python tests/test_end_to_end.py
python tests/test_inx.py
```

## License

GPL-3.0 — see [LICENSE](LICENSE).
