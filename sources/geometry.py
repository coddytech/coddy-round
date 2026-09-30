"""Outline geometry for Coddy Round.

Two kinds of work happen here:

- Bezier work on TrueType contours: parsing a glyph into line/cubic segments,
  and the corner rounding (`fillet_contour`) that turns Audiowide into the
  Coddy wordmark's letterforms.
- Polygon work through shapely, for everything that changes an outline's
  shape more deeply: bolder weights (an outward offset), the height remap that
  keeps baseline, x-height and cap height where they were, the constructed
  Cyrillic letters, and fitting the result back into smooth curves.
"""

import math

import numpy as np

import shapely
import shapely.affinity
import shapely.ops
from fontTools.cu2qu import curve_to_quadratic
from fontTools.pens.recordingPen import DecomposingRecordingPen
from shapely.geometry import LinearRing, LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient

# Turns smaller than this are tangent-continuous nodes, not corners.
MIN_TURN = math.radians(10)
# How much of a segment one fillet may consume when the segment's other end
# is not filleted (when it is, the two fillets split it half and half).
LONE_SHARE = 0.85
# cu2qu tolerance, in font units.
QUAD_ERR = 1.0


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def mul(a, k):
    return (a[0] * k, a[1] * k)


def norm(a):
    return math.hypot(a[0], a[1])


def unit(a):
    n = norm(a)
    return (a[0] / n, a[1] / n) if n else (0.0, 0.0)


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


# A segment is (p0, c1, c2, p3, is_line). Lines carry their end points as
# controls so every segment is a cubic for the maths.


def line(p0, p3):
    return (p0, p0, p3, p3, True)


def point_at(seg, t):
    p0, c1, c2, p3, is_line = seg
    if is_line:
        return lerp(p0, p3, t)
    a, b, c = lerp(p0, c1, t), lerp(c1, c2, t), lerp(c2, p3, t)
    d, e = lerp(a, b, t), lerp(b, c, t)
    return lerp(d, e, t)


def split(seg, t0, t1):
    """The part of a cubic between parameters t0 and t1."""
    p0, c1, c2, p3, is_line = seg
    if is_line:
        return line(lerp(p0, p3, t0), lerp(p0, p3, t1))

    def left(s, t):
        a, b, c, d = s
        ab, bc, cd = lerp(a, b, t), lerp(b, c, t), lerp(c, d, t)
        abc, bcd = lerp(ab, bc, t), lerp(bc, cd, t)
        return (a, ab, abc, lerp(abc, bcd, t)), (lerp(abc, bcd, t), bcd, cd, d)

    s = (p0, c1, c2, p3)
    if t1 < 1:
        s, _ = left(s, t1)
    if t0 > 0:
        _, s = left(s, t0 / t1 if t1 else 0)
    return (*s, False)


def start_tangent(seg):
    p0, c1, c2, p3, _ = seg
    for q in (c1, c2, p3):
        if norm(sub(q, p0)) > 1e-9:
            return unit(sub(q, p0))
    return (0.0, 0.0)


def end_tangent(seg):
    p0, c1, c2, p3, _ = seg
    for q in (c2, c1, p0):
        if norm(sub(p3, q)) > 1e-9:
            return unit(sub(p3, q))
    return (0.0, 0.0)


def tangent_at(seg, t):
    p0, c1, c2, p3, is_line = seg
    if is_line:
        return unit(sub(p3, p0))
    u = 1 - t
    d = add(
        add(mul(sub(c1, p0), 3 * u * u), mul(sub(c2, c1), 6 * u * t)),
        mul(sub(p3, c2), 3 * t * t),
    )
    if norm(d) < 1e-9:
        return start_tangent(seg) if t < 0.5 else end_tangent(seg)
    return unit(d)


def arc_table(seg, n=256):
    pts = [point_at(seg, i / n) for i in range(n + 1)]
    acc = [0.0]
    for i in range(n):
        acc.append(acc[-1] + norm(sub(pts[i + 1], pts[i])))
    return acc


def t_at_length(acc, s):
    """Curve parameter at arc length s (linear in the sampled table)."""
    n = len(acc) - 1
    if s <= 0:
        return 0.0
    if s >= acc[-1]:
        return 1.0
    lo, hi = 0, n
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if acc[mid] < s:
            lo = mid
        else:
            hi = mid
    span = acc[hi] - acc[lo]
    f = (s - acc[lo]) / span if span else 0
    return (lo + f) / n


def contour_segments(coords, flags, start, end):
    """TrueType contour -> list of cubic/line segments."""
    pts = [(float(x), float(y)) for x, y in coords[start : end + 1]]
    on = [bool(f & 1) for f in flags[start : end + 1]]
    n = len(pts)
    # Rotate so the contour starts on an on-curve point (insert an implied one
    # if the whole contour is off-curve).
    if not any(on):
        mid = lerp(pts[-1], pts[0], 0.5)
        pts.insert(0, mid)
        on.insert(0, True)
        n += 1
    k = on.index(True)
    pts = pts[k:] + pts[:k]
    on = on[k:] + on[:k]

    segs = []
    cur = pts[0]
    i = 1
    while i <= n:
        p, o = pts[i % n], on[i % n]
        if o:
            if norm(sub(p, cur)) > 1e-9:
                segs.append(line(cur, p))
            cur = p
            i += 1
            continue
        # Off-curve run: consecutive offs imply on-curve midpoints.
        q = p
        nxt, nxt_on = pts[(i + 1) % n], on[(i + 1) % n]
        end_pt = nxt if nxt_on else lerp(q, nxt, 0.5)
        c1 = add(cur, mul(sub(q, cur), 2 / 3))
        c2 = add(end_pt, mul(sub(q, end_pt), 2 / 3))
        segs.append((cur, c1, c2, end_pt, False))
        cur = end_pt
        i += 2 if nxt_on else 1
    return segs


def fillet_contour(segs, radius=130):
    n = len(segs)
    if n < 2:
        return segs
    lengths = [arc_table(s)[-1] for s in segs]

    # corner[i] is the node between segs[i-1] (in) and segs[i] (out).
    turn = []
    for i in range(n):
        tin, tout = end_tangent(segs[i - 1]), start_tangent(segs[i])
        cross = tin[0] * tout[1] - tin[1] * tout[0]
        dot = tin[0] * tout[0] + tin[1] * tout[1]
        turn.append(math.atan2(cross, dot))

    # TrueType puts the ink on the right of the direction of travel, so an
    # outer (convex) corner turns right: a negative angle. Concave corners
    # (turning left) keep their shape, as in the logo.
    sharp = [t < -MIN_TURN for t in turn]

    def available(seg_i, at_start):
        other = (seg_i + 1) % n if at_start else seg_i
        share = 0.5 if sharp[other] else LONE_SHARE
        return lengths[seg_i] * share

    cut = [0.0] * n  # distance trimmed back from corner i on both sides
    for i in range(n):
        if not sharp[i]:
            continue
        half = abs(turn[i]) / 2
        want = radius * math.tan(half)
        cut[i] = min(want, available(i - 1, False), available(i, True))

    out = []
    params = []
    for i, seg in enumerate(segs):
        acc = arc_table(seg)
        t0 = t_at_length(acc, cut[i]) if cut[i] else 0.0
        t1 = t_at_length(acc, acc[-1] - cut[(i + 1) % n]) if cut[(i + 1) % n] else 1.0
        # Two fillets that consume a whole segment (a square stroke end) meet
        # at the same point in its middle.
        if t1 < t0:
            t0 = t1 = (t0 + t1) / 2
        params.append((t0, t1))

    for i in range(n):
        t0, t1 = params[i]
        if cut[i]:
            pt1 = params[i - 1][1]
            p1, p2 = point_at(segs[i - 1], pt1), point_at(segs[i], t0)
            t1v, t2v = tangent_at(segs[i - 1], pt1), tangent_at(segs[i], t0)
            chord = norm(sub(p2, p1))
            if chord > 1e-6:
                cos = max(-1.0, min(1.0, t1v[0] * t2v[0] + t1v[1] * t2v[1]))
                theta = math.acos(cos)
                if theta < 1e-6:
                    out.append(line(p1, p2))
                else:
                    r = chord / (2 * math.sin(theta / 2))
                    h = 4 / 3 * math.tan(theta / 4) * r
                    out.append(
                        (p1, add(p1, mul(t1v, h)), sub(p2, mul(t2v, h)), p2, False)
                    )
        if t1 - t0 > 1e-9:
            piece = split(segs[i], t0, t1)
            if norm(sub(piece[3], piece[0])) > 1e-6:
                out.append(piece)
    return out


def draw(pen, contours):
    for segs in contours:
        if not segs:
            continue
        pen.moveTo(segs[0][0])
        for p0, c1, c2, p3, is_line in segs:
            if is_line:
                pen.lineTo(p3)
            else:
                quad = curve_to_quadratic((p0, c1, c2, p3), QUAD_ERR)
                pen.qCurveTo(*quad[1:])
        pen.closePath()




# ---------------------------------------------------------------- polygons


def _flatten(seg, tol=0.1):
    p0, c1, c2, p3, is_line = seg
    if is_line:
        return [p3]
    dd = max(norm(add(sub(p0, mul(c1, 2)), c2)), norm(add(sub(c1, mul(c2, 2)), p3)))
    n = max(1, math.ceil(math.sqrt(3 * dd / (4 * tol))))
    return [point_at(seg, i / n) for i in range(1, n + 1)]


def _winding(ring, p):
    """Winding number of a closed ring (N x 2 array, first point not
    repeated) around point p."""
    x0, y0 = ring[:, 0], ring[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    px, py = p
    left = (x1 - x0) * (py - y0) - (px - x0) * (y1 - y0)
    up = (y0 <= py) & (y1 > py) & (left > 0)
    down = (y0 > py) & (y1 <= py) & (left < 0)
    return int(up.sum() - down.sum())


def contours_to_geometry(contours):
    """Segment contours -> shapely geometry, filled exactly as TrueType fills
    them: the non-zero winding rule. The contours are split at every place
    they cross into faces, and a face is ink when the outlines wind around
    it. Deciding per contour (clockwise = ink) is not enough: Noto Sans
    Arabic has contours that cross themselves, whose overall direction says
    nothing about the counter inside them (it filled the loop of ة)."""
    rings = []
    for segs in contours:
        if not segs:
            continue
        pts = [segs[0][0]]
        for s in segs:
            pts.extend(_flatten(s))
        if len(pts) >= 4:
            rings.append(np.array(pts[:-1] if pts[0] == pts[-1] else pts, dtype=float))
    if not rings:
        return Polygon()
    lines = [LineString(np.vstack([r, r[:1]])) for r in rings]
    noded = shapely.unary_union(lines)
    faces = G_polygons(shapely.polygonize(getattr(noded, 'geoms', [noded])))
    ink = []
    for face in faces:
        if face.area < 1e-6:
            continue
        q = face.representative_point()
        if sum(_winding(r, (q.x, q.y)) for r in rings) != 0:
            ink.append(face)
    return shapely.unary_union(ink).buffer(0) if ink else Polygon()


def G_polygons(geom):
    return [g for g in getattr(geom, 'geoms', [geom]) if isinstance(g, Polygon) and not g.is_empty]


def glyph_geometry(glyph_set, name):
    """A glyph (composites decomposed) as shapely geometry."""
    pen = DecomposingRecordingPen(glyph_set)
    glyph_set[name].draw(pen)
    return contours_to_geometry(recording_to_contours(pen.value))


def recording_to_contours(value):
    contours, cur, segs, start = [], None, [], None
    for op, args in value:
        if op == 'moveTo':
            cur = start = args[0]
            segs = []
        elif op == 'lineTo':
            if norm(sub(args[0], cur)) > 1e-9:
                segs.append(line(cur, args[0]))
            cur = args[0]
        elif op == 'qCurveTo':
            pts = list(args)
            if pts[-1] is None:  # closed all-off-curve contour
                pts = pts[:-1]
                cur = start = lerp(pts[-1], pts[0], 0.5)
                pts.append(cur)
            offs, end = pts[:-1], pts[-1]
            for i, q in enumerate(offs):
                e = end if i == len(offs) - 1 else lerp(q, offs[i + 1], 0.5)
                c1 = add(cur, mul(sub(q, cur), 2 / 3))
                c2 = add(e, mul(sub(q, e), 2 / 3))
                segs.append((cur, c1, c2, e, False))
                cur = e
        elif op == 'curveTo':
            c1, c2, e = args
            segs.append((cur, c1, c2, e, False))
            cur = e
        elif op in ('closePath', 'endPath'):
            if cur is not None and start is not None and norm(sub(cur, start)) > 1e-9:
                segs.append(line(cur, start))
            if segs:
                contours.append(segs)
            segs, cur = [], None
    return contours


def polygons(geom):
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    return [g for g in getattr(geom, 'geoms', []) if isinstance(g, Polygon) and not g.is_empty]


def clean(geom):
    return MultiPolygon([p for p in polygons(geom.buffer(0)) if p.area > 50]).buffer(0)


def embolden(geom, dx, dy):
    """Grow the ink by dx horizontally and dy vertically (mitred corners, so
    the corner rounding that follows sees the same sharp corners the
    Regular had)."""
    if dx <= 0 and dy <= 0:
        return geom
    k = dx / dy if dy > 0 else 1.0
    g = shapely.affinity.scale(geom, 1, k, origin=(0, 0))
    g = g.buffer(dx, join_style='mitre', mitre_limit=8)
    return shapely.affinity.scale(g, 1, 1 / k, origin=(0, 0))


def edge_keys(geom, zones, grow):
    """Height keys for `remap_y`: every horizontal edge that sits on an
    alignment zone keeps sitting on it after the outline has grown by `grow`
    vertically (a top edge came up by grow, a bottom edge went down)."""
    tops, bottoms = set(), set()
    for poly in polygons(orient_geometry(geom, 1.0)):
        for ring in [poly.exterior, *poly.interiors]:
            c = list(ring.coords)
            for (x0, y0), (x1, y1) in zip(c, c[1:]):
                if abs(y1 - y0) > 1.5 or abs(x1 - x0) < 20:
                    continue
                y = (y0 + y1) / 2
                for z in zones:
                    if abs(y - z) <= 2:
                        # Ink is on the left of a counter-clockwise walk.
                        (bottoms if x1 > x0 else tops).add(z)
    keys = [(z + grow, z) for z in tops] + [(z - grow, z) for z in bottoms if z not in tops]
    keys.sort(key=lambda k: k[1])
    out = []
    for k in keys:
        if not out or (k[0] > out[-1][0] + 1 and k[1] > out[-1][1]):
            out.append(k)
    return out


def remap_y(geom, keys):
    """Piecewise-linear vertical map through (from, to) keys; slope 1 outside."""
    if not keys:
        return geom

    def f(y):
        if y <= keys[0][0]:
            return y - keys[0][0] + keys[0][1]
        if y >= keys[-1][0]:
            return y - keys[-1][0] + keys[-1][1]
        for (a0, b0), (a1, b1) in zip(keys, keys[1:]):
            if a0 <= y <= a1:
                return b0 + (y - a0) * (b1 - b0) / (a1 - a0)
        return y

    return shapely.ops.transform(lambda x, y, z=None: (x, [f(v) for v in y]) if hasattr(y, '__len__') else (x, f(y)), geom)


def orient_geometry(geom, sign):
    return MultiPolygon([orient(p, sign) for p in polygons(geom)]) if polygons(geom) else geom


# ------------------------------------------------------------ curve fitting

# Edges longer than this in a finely flattened outline are straight lines.
LINE_LEN = 50
# A vertex turning more than this is a corner.
CORNER_TURN = math.radians(25)
FIT_ERR = 0.6


def _bez(p0, c1, c2, p3, t):
    u = 1 - t
    return (
        u * u * u * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * p3[1],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _params(pts):
    d = [0.0]
    for a, b in zip(pts, pts[1:]):
        d.append(d[-1] + norm(sub(b, a)))
    total = d[-1] or 1
    return [v / total for v in d]


def _generate(pts, u, t1, t2):
    p0, p3 = pts[0], pts[-1]
    c = [[0.0, 0.0], [0.0, 0.0]]
    x = [0.0, 0.0]
    for p, t in zip(pts, u):
        s = 1 - t
        a0 = mul(t1, 3 * s * s * t)
        a1 = mul(t2, -3 * s * t * t)
        c[0][0] += _dot(a0, a0)
        c[0][1] += _dot(a0, a1)
        c[1][1] += _dot(a1, a1)
        base = add(mul(p0, s * s * s + 3 * s * s * t), mul(p3, 3 * s * t * t + t * t * t))
        tmp = sub(p, base)
        x[0] += _dot(a0, tmp)
        x[1] += _dot(a1, tmp)
    c[1][0] = c[0][1]
    det = c[0][0] * c[1][1] - c[0][1] * c[1][0]
    dist = norm(sub(p3, p0))
    if abs(det) > 1e-12:
        al = (x[0] * c[1][1] - x[1] * c[0][1]) / det
        ar = (c[0][0] * x[1] - c[1][0] * x[0]) / det
    else:
        al = ar = 0
    # Handles that are tiny, or far longer than the chord, mean the least
    # squares solve went wrong (few, nearly collinear points): a handle of
    # thousands of units still passes through every data point, as a loop
    # far outside the letter (medial Arabic ع at Bold). Use the standard
    # one-third estimate instead.
    if not (dist * 1e-3 < al < dist * 3) or not (dist * 1e-3 < ar < dist * 3):
        al = ar = dist / 3
    return (p0, add(p0, mul(t1, al)), sub(p3, mul(t2, ar)), p3)


def _max_error(pts, bez, u):
    worst, idx = 0.0, len(pts) // 2
    for i, (p, t) in enumerate(zip(pts, u)):
        e = norm(sub(_bez(*bez, t), p))
        if e > worst:
            worst, idx = e, i
    return worst, idx


def _reparam(pts, bez, u):
    p0, c1, c2, p3 = bez
    d1 = [mul(sub(c1, p0), 3), mul(sub(c2, c1), 3), mul(sub(p3, c2), 3)]
    d2 = [mul(sub(d1[1], d1[0]), 2), mul(sub(d1[2], d1[1]), 2)]
    out = []
    for p, t in zip(pts, u):
        q = _bez(p0, c1, c2, p3, t)
        s = 1 - t
        q1 = add(add(mul(d1[0], s * s), mul(d1[1], 2 * s * t)), mul(d1[2], t * t))
        q2 = add(mul(d2[0], s), mul(d2[1], t))
        diff = sub(q, p)
        den = _dot(q1, q1) + _dot(diff, q2)
        out.append(min(1.0, max(0.0, t - _dot(diff, q1) / den)) if abs(den) > 1e-12 else t)
    return out


def _hugs(pts, bez, err):
    """Does the whole curve stay on the data, not only at the data points?
    A curve can pass through every point and still loop away between them."""
    path = LineString(pts)
    return all(path.distance(Point(_bez(*bez, k / 16))) <= err * 3 for k in range(1, 16))


def fit_run(pts, t1, t2, err=FIT_ERR, depth=0):
    if len(pts) == 2 or norm(sub(pts[-1], pts[0])) < 1e-6:
        d = norm(sub(pts[-1], pts[0])) / 3
        return [(pts[0], add(pts[0], mul(t1, d)), sub(pts[-1], mul(t2, d)), pts[-1], False)]
    u = _params(pts)
    bez = _generate(pts, u, t1, t2)
    worst, idx = _max_error(pts, bez, u)
    if worst < err and _hugs(pts, bez, err):
        return [(*bez, False)]
    if worst < err * 6:
        for _ in range(6):
            u = _reparam(pts, bez, u)
            bez = _generate(pts, u, t1, t2)
            worst, idx = _max_error(pts, bez, u)
            if worst < err and _hugs(pts, bez, err):
                return [(*bez, False)]
    idx = min(max(idx, 1), len(pts) - 2)
    tc = unit(sub(pts[idx + 1], pts[idx - 1]))
    if depth > 30:
        return [line(a, b) for a, b in zip(pts, pts[1:])]
    return fit_run(pts[: idx + 1], t1, tc, err, depth + 1) + fit_run(pts[idx:], tc, t2, err, depth + 1)


def fit_ring(coords):
    """Closed polyline (a finely flattened outline) -> lines and cubics."""
    pts = []
    for p in coords:
        if not pts or norm(sub(p, pts[-1])) > 0.05:
            pts.append(p)
    if len(pts) > 1 and norm(sub(pts[0], pts[-1])) <= 0.05:
        pts.pop()
    n = len(pts)
    if n < 3:
        return []
    edges = [sub(pts[(i + 1) % n], pts[i]) for i in range(n)]
    is_line = [norm(e) >= LINE_LEN for e in edges]

    def turn(i):
        a, b = unit(edges[i - 1]), unit(edges[i])
        return abs(math.atan2(a[0] * b[1] - a[1] * b[0], _dot(a, b)))

    corner = [turn(i) > CORNER_TURN for i in range(n)]
    # Break vertices: corners and both ends of every straight edge.
    brk = [corner[i] or is_line[i] or is_line[i - 1] for i in range(n)]
    # A closed run needs two ends: with one break (a teardrop counter, one
    # corner) the run would start and end on the same point and fit to
    # nothing, which is how the loop of Arabic ة lost its counter.
    if sum(brk) < 2:
        k = brk.index(True) if any(brk) else 0
        brk[k] = brk[(k + n // 2) % n] = True
    first = brk.index(True)
    order = [(first + k) % n for k in range(n)]
    out = []
    i = 0
    while i < n:
        v = order[i]
        if is_line[v]:
            out.append(line(pts[v], pts[(v + 1) % n]))
            i += 1
            continue
        run = [v]
        j = i + 1
        while j <= n:
            w = order[j % n]
            run.append(w)
            if brk[w]:
                break
            j += 1
        rp = [pts[k] for k in run]

        def tangent_in(k):  # direction arriving at vertex k (for a run start)
            if corner[k] and not is_line[k - 1]:
                return unit(edges[k])
            if is_line[k - 1]:
                return unit(edges[k - 1]) if not corner[k] else unit(edges[k])
            return unit(add(unit(edges[k - 1]), unit(edges[k])))

        def tangent_out(k):  # direction leaving the run at vertex k
            if corner[k]:
                return unit(edges[k - 1])
            if is_line[k]:
                return unit(edges[k])
            return unit(add(unit(edges[k - 1]), unit(edges[k])))

        t1 = tangent_in(run[0])
        t2 = tangent_out(run[-1])
        out.extend(fit_run(rp, t1, t2))
        i = j
    return out


def fit_deviation(geom, contours):
    """How far any fitted curve strays from the outline it was fitted to,
    sampled along each curve, in font units."""
    edge = geom.boundary
    worst = 0.0
    for segs in contours:
        for s in segs:
            if s[4]:
                continue
            for k in range(1, 16):
                worst = max(worst, edge.distance(Point(point_at(s, k / 16))))
    return worst


def geometry_to_contours(geom):
    """Shapely geometry -> TrueType-direction segment contours (ink on the
    right: outer contours clockwise, counters counter-clockwise)."""
    contours = []
    for poly in polygons(orient_geometry(geom, -1.0)):
        poly = poly.simplify(0.08, preserve_topology=True)
        for ring in [poly.exterior, *poly.interiors]:
            segs = fit_ring(list(ring.coords))
            if segs:
                contours.append(segs)
    return contours


# ------------------------------------------------------- stroke drawing kit


def stroke(points, radii=None, width=260, cap='flat'):
    """A monoline stroke along a centreline, the way Audiowide is built: each
    interior vertex may carry a centreline corner radius (235 gives
    Audiowide's rounded outer corners: outer radius 365, inner 105)."""
    radii = radii or [0] * len(points)
    line_pts = [points[0]]
    for i in range(1, len(points) - 1):
        r = radii[i]
        v = points[i]
        if not r:
            line_pts.append(v)
            continue
        u_in = unit(sub(v, points[i - 1]))
        u_out = unit(sub(points[i + 1], v))
        cross = u_in[0] * u_out[1] - u_in[1] * u_out[0]
        th = math.atan2(cross, _dot(u_in, u_out))
        if abs(th) < 1e-6:
            line_pts.append(v)
            continue
        d = r * math.tan(abs(th) / 2)
        a = sub(v, mul(u_in, d))
        nrm = (-u_in[1], u_in[0]) if th > 0 else (u_in[1], -u_in[0])
        c = add(a, mul(nrm, r))
        a0 = math.atan2(a[1] - c[1], a[0] - c[0])
        steps = max(8, int(abs(th) / math.radians(2)))
        for k in range(steps + 1):
            ang = a0 + th * k / steps
            line_pts.append((c[0] + r * math.cos(ang), c[1] + r * math.sin(ang)))
    line_pts.append(points[-1])
    return LineString(line_pts).buffer(
        width / 2, cap_style=cap, join_style='mitre', mitre_limit=10
    )


def union(*geoms):
    return shapely.unary_union([g for g in geoms if g is not None and not g.is_empty])


def clip(geom, x0, y0, x1, y1):
    return geom.intersection(box(x0, y0, x1, y1))


def cut(geom, x0, y0, x1, y1):
    return geom.difference(box(x0, y0, x1, y1))


def shift(geom, dx=0, dy=0):
    return shapely.affinity.translate(geom, dx, dy)


def mirror(geom, advance):
    return shapely.affinity.scale(geom, -1, 1, origin=(advance / 2, 0))
