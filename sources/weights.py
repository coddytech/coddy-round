"""How a heavier weight is drawn: an outward offset of the unrounded outline
that keeps what makes the letters Audiowide.

A plain offset closes Audiowide's designed slits (E's and F's arm and d's
bowl stop 104 units short of the stem, a's slot, the spiral of @), merges
separate pieces (the dots of Arabic, the halves of %) and fills counters. The
functions here grow every glyph at full weight while keeping those open, the
way a type designer would: the stroke that ENDS at a slit is shortened, never
the one that runs past it. Anything they still cannot keep falls back to as
much extra weight as leaves the glyph's shape (its pieces and counters)
exactly as it was.
"""

import shapely
import shapely.geometry
import shapely.ops

import geometry as G


def topology(geom):
    polys = G.polygons(geom)
    return len(polys), sum(len(p.interiors) for p in polys)


# A gap narrower than this after bolding reads as closed at text sizes.
# Arabic sets its dots much closer to the letters than Latin sets anything,
# so the companions get their own floor.
MIN_GAP = 70
# Background narrower than twice this is a designed slit (Audiowide's E and F
# arms and d's bowl stop 104 short of the stem; a's slot is 117; the spiral
# of @; an Arabic dot over its letter), not open space.
SLIT_RADIUS = 60
# ...unless it is this small: that is just the inside of a concave corner.
SLIT_MIN_AREA = 4000


def _narrow_background(geom):
    x0, y0, x1, y1 = geom.bounds
    frame = shapely.geometry.box(x0 - 400, y0 - 400, x1 + 400, y1 + 400)
    bg = frame.difference(geom)
    opened = bg.buffer(-SLIT_RADIUS).buffer(SLIT_RADIUS)
    narrow = [c for c in G.polygons(bg.difference(opened)) if c.area >= SLIT_MIN_AREA]
    return narrow, opened


def _local_box(centre, u, n, u0, u1, n0, n1):
    cx, cy = centre
    pts = [(u0, n0), (u1, n0), (u1, n1), (u0, n1)]
    return shapely.geometry.Polygon(
        [(cx + a * u[0] + b * n[0], cy + a * u[1] + b * n[1]) for a, b in pts]
    )


def _centreline(c, width):
    """The medial axis of a slit, as simplified polylines (an L-shaped slit
    comes back as its two legs)."""
    pts = shapely.segmentize(c.exterior, 6).coords
    edges = shapely.voronoi_polygons(shapely.geometry.MultiPoint(pts), only_edges=True)
    inner = [
        e for e in getattr(edges, 'geoms', [edges])
        if c.contains(e) and min(c.boundary.distance(shapely.geometry.Point(q)) for q in e.coords) > width * 0.3
    ]
    if not inner:
        return []
    merged = shapely.line_merge(G.union(*inner))
    lines = [g for g in getattr(merged, 'geoms', [merged]) if g.length > width * 0.5]
    return [list(g.simplify(width * 0.25).coords) for g in lines]


def _band(geom, a, b, w, dx, dy, target, open_a, open_b):
    """Keep-open band for one straight piece a->b of a slit of width w:
    against the side that runs on (more ink behind it), reaching past any
    end that opens into a counter so the ending stroke's grown corners are
    caught too."""
    u = G.unit(G.sub(b, a))
    n = (-u[1], u[0])
    grow_n = ((dx * n[0]) ** 2 + (dy * n[1]) ** 2) ** 0.5
    if w - 2 * grow_n >= target:
        return None
    half = G.norm(G.sub(b, a)) / 2
    centre = G.lerp(a, b, 0.5)
    ext = half + 300

    def behind(sign):
        strip = _local_box(centre, u, n, -ext, ext, sign * w / 2, sign * (w / 2 + 250))
        return strip.intersection(geom).area

    side = 1 if behind(1) >= behind(-1) else -1
    more = w / 2 + max(dx, dy) * 1.5 + 20
    lo = -half - (more if open_a else 0)
    hi = half + (more if open_b else 0)
    edge = side * (w / 2 - grow_n)
    return _local_box(centre, u, n, lo, hi, edge, edge - side * target)


def _separator(r, o, dx, dy, target):
    """Between two separate pieces: `r` (the one with more ink near the gap)
    grows fully; `o` is cut back to stay `target` clear of it."""
    near = G.embolden(r, dx + target, dy + target).difference(G.embolden(r, dx, dy))
    return near.intersection(G.embolden(o, dx, dy).buffer(1))


def slits(geom, dx, dy, min_gap):
    """What must stay open when a glyph grows by (dx, dy).

    A slit (E's and F's arm, d's and P's bowl bar, a's slot, the spiral of @)
    sits between a stroke that runs on past it and a stroke that ENDS at it.
    A type designer keeps it by shortening the ending stroke, never by
    thinning the one that runs on: between two separate pieces the one with
    more ink near the gap grows fully and the other is cut back; inside one
    piece each straight leg of the slit keeps a band open against the side
    with more ink behind it. A wedge inside one piece (the notches of M) is
    not a slit: bolding may fill it."""
    parts = G.polygons(geom)
    narrow, opened = _narrow_background(geom)
    keep = []
    reach = 2 * max(dx, dy) + min_gap
    for i, a in enumerate(parts):
        for b in parts[i + 1:]:
            d = a.distance(b)
            if d >= reach:
                continue
            zone = a.buffer(d + 300).intersection(b.buffer(d + 300))
            ra, rb = a.intersection(zone).area, b.intersection(zone).area
            r, o = (a, b) if ra >= rb else (b, a)
            keep.append(_separator(r, o, dx, dy, min(min_gap, max(d, 1))))
    for c in narrow:
        if sum(1 for p in parts if p.distance(c) < 1) >= 2:
            continue  # between separate pieces: handled above
        centre = shapely.ops.polylabel(c, tolerance=1)
        w = 2 * c.boundary.distance(centre)
        if c.area / max(c.length / 2 - w, 1) < 0.7 * w:
            continue  # a wedge
        target = min(min_gap, w)
        core = c.buffer(-(w - target) / 2) if w > target else c
        for axis in _centreline(c, w):
            for i in range(len(axis) - 1):
                ends = [axis[i], axis[i + 1]]
                is_open = [opened.distance(shapely.geometry.Point(q)) < w for q in ends]
                band = _band(geom, ends[0], ends[1], w, dx, dy, target, is_open[0], is_open[1])
                if band is None:
                    continue
                keep.append(band)
                # Where two legs meet (the corner of a's L), bridge the bands
                # with the middle of the slit so no ink crosses the joint.
                for q, o in zip(ends, is_open):
                    if not o:
                        keep.append(core.intersection(shapely.geometry.Point(q).buffer(w * 1.5)))
    return G.union(*keep) if keep else None


def fill_new_holes(grown, original, limit=6000):
    """Bolding can close a wedge into a pinhole; fill holes the Regular did
    not have, when they are that small."""
    holes = [shapely.geometry.Polygon(r) for p in G.polygons(original) for r in p.interiors]
    out = []
    for p in G.polygons(grown):
        rings = [r for r in p.interiors
                 if shapely.geometry.Polygon(r).area > limit
                 or any(h.intersects(shapely.geometry.Polygon(r)) for h in holes)]
        out.append(shapely.geometry.Polygon(p.exterior, rings))
    return G.union(*out)


def grow(geom, dx, dy, zones, keep):
    keys = G.edge_keys(geom, zones, dy) if zones and dy > 0 else None
    grown = G.embolden(geom, dx, dy)
    if keys:
        grown = G.remap_y(grown, keys)
    if keep is not None and not keep.is_empty:
        grown = grown.difference(G.remap_y(keep, keys) if keys else keep)
    return fill_new_holes(G.clean(grown), geom)


# Parts at most this big are dots (Arabic nuqat, a dieresis, a colon).
DOT_AREA = 70000


def grow_dots(geom, dx, dy, zones, min_gap):
    """A glyph made only of dots: grow every dot fully and spread them from
    their common centre just enough that none touches, which is what a bold
    Arabic does with its two- and three-dot marks."""
    parts = G.polygons(geom)
    gap0 = min(a.distance(b) for i, a in enumerate(parts) for b in parts[i + 1:])
    need = min(min_gap, gap0)
    cx, cy = geom.centroid.x, geom.centroid.y
    grown = [grow(p, dx, dy, zones, None) for p in parts]
    for k in [1 + i * 0.05 for i in range(41)]:
        moved = [G.shift(g, (p.centroid.x - cx) * (k - 1), (p.centroid.y - cy) * (k - 1))
                 for g, p in zip(grown, parts)]
        if min(a.distance(b) for i, a in enumerate(moved) for b in moved[i + 1:]) >= need:
            return G.union(*moved)
    return None


def safe_grow(geom, dx, dy, zones, min_gap=MIN_GAP):
    """Bold a glyph at full weight with its slits held open; if the shape
    would still change (a counter closing, parts merging), fall back to as
    much extra weight as keeps it."""
    if dx <= 0:
        return geom
    parts = G.polygons(geom)
    if len(parts) > 1 and max(p.area for p in parts) <= DOT_AREA:
        dots = grow_dots(geom, dx, dy, zones, min_gap)
        if dots is not None:
            return dots
    keep = slits(geom, dx, dy, min_gap)
    want = topology(geom)
    probe = min_gap * 0.4
    want_near = topology(geom.buffer(probe))

    def ok(s):
        g = grow(geom, dx * s, dy * s, zones, keep)
        return topology(g) == want and topology(g.buffer(probe)) == want_near

    if ok(1.0):
        full = grow(geom, dx, dy, zones, keep)
        # Holding slits open must never cost a glyph its weight: two dots
        # side by side are not a stem and its arm, and cutting one back
        # would halve it. Below half the intended gain, grow evenly instead.
        if full.area >= G.embolden(geom, dx / 2, dy / 2).area:
            return full
        keep = None

    def search():
        lo, hi = 0.0, 1.0
        for _ in range(10):
            mid = (lo + hi) / 2
            if ok(mid):
                lo = mid
            else:
                hi = mid
        return grow(geom, dx * lo, dy * lo, zones, keep)

    result = search()
    if keep is not None and result.area < geom.area:
        keep = None
        result = search()
    return result


def work(task):
    """One glyph through offset -> height remap -> shift -> fit -> round.
    Also returns how far the fitted curves stray from the outline, so the
    build can refuse a glyph with a runaway curve."""
    name, wkb, dx, dy, zones, shift_x, radius, min_gap = task
    geom = shapely.from_wkb(wkb)
    grown = safe_grow(geom, dx, dy, zones, min_gap)
    if shift_x:
        grown = G.shift(grown, shift_x, 0)
    fitted = G.geometry_to_contours(grown)
    deviation = G.fit_deviation(grown, fitted)
    return name, [G.fillet_contour(c, radius) for c in fitted], deviation
