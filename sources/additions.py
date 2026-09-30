"""Glyphs Audiowide never had, drawn in its own construction.

Audiowide covers Latin only. Everything here is built from Audiowide's parts
or with its drawing rules, measured from the font:

- a monoline stroke 260 units wide (`geometry.stroke`),
- rounded outer corners on a 235-unit centreline radius (outer 365, inner
  105, the same as the bowls of C, E, O, U),
- cap height 1434, x-height 1081, middle bars at 586..848 (caps) and
  410..670 (lowercase), descenders at -300 for the Cyrillic tails.

Letters that are the same shape in Latin and Cyrillic are composites of the
Latin glyph, so a fix to one is a fix to both. The shapes are still UNROUNDED
here: the corner rounding (and, for heavier weights, the offset) happens in
build.py exactly as it does for the Latin letters, so the two scripts can
never drift apart.

Each entry is (target file, glyph name, unicodes, spec), where spec is either
('geom', shapely geometry, advance) or ('comp', [(glyph, dx, dy)], advance).
"""

from geometry import clip, cut, glyph_geometry, mirror, remap_y, shift, stroke, union
from shapely.geometry import box

CAP, XH, S = 1434, 1081, 260
H = S // 2
T, B = CAP - H, H  # cap-height centrelines of the top and bottom strokes
M = 717  # cap middle bar centreline
t, m = XH - H, 540  # lowercase top-stroke and middle-bar centrelines
R = 235  # Audiowide's corner radius on the centreline
r = 180  # the same corner where a lowercase bowl is too short for 235
TAIL = -300


def rrect(x0, y0, x1, y1, rad):
    return box(x0 + rad, y0 + rad, x1 - rad, y1 - rad).buffer(rad, quad_segs=32)


def ring(x0, y0, x1, y1):
    """An O-shaped bowl: outer corners 365, inner 105, like Audiowide's O."""
    return rrect(x0, y0, x1, y1, R + H).difference(rrect(x0 + S, y0 + S, x1 - S, y1 - S, R - H))


def band(g, lo, hi):
    return clip(g, -500, lo, 4000, hi)


def build(lat, ext):
    """lat/ext: glyph sets of the unrounded Latin and Latin-Ext sources."""
    L = lambda n: glyph_geometry(lat, n)  # noqa: E731
    W = lambda n: lat[n].width  # noqa: E731
    out = []

    def geom(target, name, uni, g, adv):
        out.append((target, name, uni, ('geom', g, adv)))

    def comp(target, name, uni, parts, adv):
        out.append((target, name, uni, ('comp', parts, adv)))

    cyr = 'cyrillic'

    # ---- Cyrillic capitals ------------------------------------------------
    same = {
        0x0410: 'A', 0x0412: 'B', 0x0415: 'E', 0x041A: 'K', 0x041C: 'M',
        0x041D: 'H', 0x041E: 'O', 0x0420: 'P', 0x0421: 'C', 0x0422: 'T',
        0x0425: 'X', 0x0417: 'three', 0x0401: 'Edieresis', 0x0406: 'I',
        0x0407: 'Idieresis',
        0x0430: 'a', 0x0435: 'e', 0x043E: 'o', 0x0440: 'p', 0x0441: 'c',
        0x0443: 'y', 0x0445: 'x', 0x0433: 'r', 0x043F: 'n', 0x0451: 'edieresis',
        0x0456: 'i', 0x0457: 'idieresis',
    }
    for u, base in same.items():
        comp(cyr, f'uni{u:04X}', [u], [(base, 0, 0)], W(base))

    geom(cyr, 'uni0411', [0x0411], stroke(
        [(1447, T), (279, T), (279, B), (1452, B), (1452, M), (279, M)],
        [0, R, R, R, R, 0]), 1657)  # Б
    geom(cyr, 'uni0413', [0x0413], cut(L('F'), 412, -10, 2000, 1000), W('F'))  # Г
    geom(cyr, 'uni0414', [0x0414], band(union(  # Д
        stroke([(200, TAIL), (200, B), (1530, B), (1530, TAIL)]),
        stroke([(400, B), (400, T), (1330, T), (1330, B)], [0, R, R, 0]),
    ), TAIL, CAP), 1730)
    geom(cyr, 'uni0416', [0x0416], zhe(1050, M, 820, 717, CAP), 2100)  # Ж
    N = L('N')
    geom(cyr, 'uni0418', [0x0418], mirror(N, W('N')), W('N'))  # И
    # The breve lives in the Latin-Ext source; the Cyrillic file needs its own.
    geom(cyr, 'breve', [], glyph_geometry(ext, 'breve'), ext['breve'].width)
    comp(cyr, 'uni0419', [0x0419], [('uni0418', 0, 0), ('breve', round(W('N') / 2 - 570.5), 350)], W('N'))
    geom(cyr, 'uni041B', [0x041B], stroke(  # Л
        [(30, B), (480, B), (480, T), (1480, T), (1480, 0)], [0, R, R, R, 0]), 1760)
    geom(cyr, 'uni041F', [0x041F], cut(L('A'), 385, -10, 1297, 1050), W('A'))  # П
    geom(cyr, 'uni0423', [0x0423], union(  # У
        stroke([(252, CAP), (252, 602), (1426, 602)], [0, R, 0]),
        stroke([(1426, CAP), (1426, B), (481, B)], [0, R, 0]),
    ), 1707)
    geom(cyr, 'uni0424', [0x0424], union(  # Ф
        stroke([(850, 0), (850, CAP)]), ring(120, 180, 1580, 1254)), 1700)
    geom(cyr, 'uni0426', [0x0426], band(union(  # Ц
        stroke([(252, CAP), (252, B), (1426, B)], [0, R, 0]),
        stroke([(1426, CAP), (1426, TAIL)]),
    ), TAIL, CAP), 1707)
    geom(cyr, 'uni0427', [0x0427], union(  # Ч
        stroke([(250, CAP), (250, 602), (1350, 602)], [0, R, 0]),
        stroke([(1350, CAP), (1350, 0)]),
    ), 1600)
    geom(cyr, 'uni0428', [0x0428], union(  # Ш
        stroke([(280, CAP), (280, B), (1820, B), (1820, CAP)], [0, R, R, 0]),
        stroke([(1050, CAP), (1050, B)]),
    ), 2100)
    geom(cyr, 'uni0429', [0x0429], band(union(  # Щ
        stroke([(280, CAP), (280, B), (1820, B)], [0, R, 0]),
        stroke([(1050, CAP), (1050, B)]),
        stroke([(1820, CAP), (1820, TAIL)]),
    ), TAIL, CAP), 2100)
    geom(cyr, 'uni042A', [0x042A], stroke(  # Ъ
        [(60, T), (460, T), (460, B), (1500, B), (1500, M), (460, M)],
        [0, R, R, R, R, 0]), 1757)
    geom(cyr, 'uni042B', [0x042B], union(  # Ы
        stroke([(280, CAP), (280, B), (1150, B), (1150, M), (280, M)], [0, R, R, R, 0]),
        stroke([(1700, CAP), (1700, 0)]),
    ), 1980)
    geom(cyr, 'uni042C', [0x042C], stroke(  # Ь
        [(280, CAP), (280, B), (1300, B), (1300, M), (280, M)], [0, R, R, R, 0]), 1580)
    C = L('C')
    geom(cyr, 'uni042D', [0x042D], union(  # Э
        mirror(C, W('C')), stroke([(430, M), (1270, M)])), W('C'))
    geom(cyr, 'uni0404', [0x0404], union(C, stroke([(262, M), (1100, M)])), W('C'))  # Є
    geom(cyr, 'uni042E', [0x042E], union(  # Ю
        stroke([(280, 0), (280, CAP)]), stroke([(280, M), (700, M)]),
        ring(620, -20, 1870, 1454)), 1990)
    geom(cyr, 'uni042F', [0x042F], mirror(L('R'), W('R')), W('R'))  # Я

    # ---- Cyrillic lowercase -----------------------------------------------
    b = cut(L('b'), 0, 1000, 480, 1700)
    geom(cyr, 'uni0431', [0x0431], union(  # б
        b, stroke([(246, 900), (246, 1408), (1250, 1408)], [0, R, 0])), W('b'))
    geom(cyr, 'uni0432', [0x0432], union(  # в
        stroke([(246, m), (246, t), (1100, t), (1100, m), (246, m)], [0, r, r, r, 0]),
        stroke([(246, m), (246, B), (1180, B), (1180, m), (246, m)], [0, r, r, r, 0]),
    ), 1410)
    geom(cyr, 'uni0434', [0x0434], band(union(  # д
        stroke([(180, TAIL), (180, B), (1300, B), (1300, TAIL)]),
        stroke([(360, B), (360, t), (1120, t), (1120, B)], [0, r, r, 0]),
    ), TAIL, XH), 1480)
    geom(cyr, 'uni0436', [0x0436], zhe(847, m, 620, 540, XH), 1694)  # ж
    geom(cyr, 'uni0437', [0x0437], union(  # з
        stroke([(110, t), (1000, t), (1000, B), (110, B)], [0, r, r, 0]),
        stroke([(300, m), (1000, m)]),
    ), 1240)
    i_ = clip(union(  # и
        stroke([(252, 0), (252, XH)]), stroke([(1190, 0), (1190, XH)]),
        stroke([(252, -50), (1190, XH + 50)]),
    ), 122, 0, 1320, XH)
    geom(cyr, 'uni0438', [0x0438], i_, 1426)
    comp(cyr, 'uni0439', [0x0439], [('uni0438', 0, 0), ('breve', 142, 0)], 1426)
    geom(cyr, 'uni043A', [0x043A], cut(L('k'), 0, 1090, 520, 1700), W('k'))  # к
    geom(cyr, 'uni043B', [0x043B], stroke(  # л
        [(30, B), (400, B), (400, t), (1220, t), (1220, 0)], [0, r, r, r, 0]), 1470)
    geom(cyr, 'uni043C', [0x043C], band(stroke(  # м
        [(250, 0), (250, t), (850, 300), (1450, t), (1450, 0)]), 0, XH), 1700)
    geom(cyr, 'uni043D', [0x043D], union(  # н
        stroke([(252, 0), (252, XH)]), stroke([(1190, 0), (1190, XH)]),
        stroke([(252, m), (1190, m)])), 1426)
    geom(cyr, 'uni0442', [0x0442], union(  # т
        stroke([(100, t), (1200, t)]), stroke([(650, t), (650, 0)])), 1300)
    geom(cyr, 'uni0444', [0x0444], union(  # ф
        stroke([(790, -469), (790, 1538)]), ring(150, 0, 1430, XH)), 1580)
    geom(cyr, 'uni0446', [0x0446], band(union(  # ц
        L('u'), stroke([(1174, B), (1174, TAIL)])), TAIL, XH), W('u'))
    geom(cyr, 'uni0447', [0x0447], union(  # ч
        stroke([(246, XH), (246, 470), (1150, 470)], [0, r, 0]),
        stroke([(1150, XH), (1150, 0)])), 1400)
    geom(cyr, 'uni0448', [0x0448], union(  # ш
        stroke([(250, XH), (250, B), (1650, B), (1650, XH)], [0, R, R, 0]),
        stroke([(950, XH), (950, B)])), 1900)
    geom(cyr, 'uni0449', [0x0449], band(union(  # щ
        stroke([(250, XH), (250, B), (1650, B)], [0, R, 0]),
        stroke([(950, XH), (950, B)]), stroke([(1650, XH), (1650, TAIL)]),
    ), TAIL, XH), 1900)
    geom(cyr, 'uni044A', [0x044A], stroke(  # ъ
        [(50, t), (400, t), (400, B), (1250, B), (1250, m), (400, m)],
        [0, r, r, r, r, 0]), 1400)
    geom(cyr, 'uni044B', [0x044B], union(  # ы
        stroke([(250, XH), (250, B), (1000, B), (1000, m), (250, m)], [0, r, r, r, 0]),
        stroke([(1500, XH), (1500, 0)])), 1780)
    geom(cyr, 'uni044C', [0x044C], stroke(  # ь
        [(250, XH), (250, B), (1100, B), (1100, m), (250, m)], [0, r, r, r, 0]), 1360)
    c = L('c')
    geom(cyr, 'uni044D', [0x044D], union(  # э
        mirror(c, W('c')), stroke([(360, m), (1030, m)])), W('c'))
    geom(cyr, 'uni0454', [0x0454], union(c, stroke([(230, m), (900, m)])), W('c'))  # є
    geom(cyr, 'uni044E', [0x044E], union(  # ю
        stroke([(246, 0), (246, XH)]), stroke([(246, m), (620, m)]),
        shift(L('o'), 460)), 1857)
    geom(cyr, 'uni044F', [0x044F], clip(union(  # я
        stroke([(1150, XH), (1150, 0)]),
        stroke([(1150, t), (246, t), (246, 470), (1150, 470)], [0, r, r, 0]),
        stroke([(1020, 470), (20, -50)]),
    ), 60, 0, 1400, XH), 1400)

    comp(cyr, 'uni2116', [0x2116], [('N', 0, 0), ('ordmasculine', 1560, 0)], 2810)  # №

    # ---- Latin additions --------------------------------------------------
    geom('latin-ext', 'uni1E9E', [0x1E9E], remap_y(L('germandbls'), [(0, 0), (1542, CAP)]), W('germandbls'))  # ẞ
    comp('latin-ext', 'uni0218', [0x0218], [('S', 0, 0), ('uni0326', 620, 0)], W('S'))  # Ș
    comp('latin-ext', 'uni0219', [0x0219], [('s', 0, 0), ('uni0326', 436, 0)], W('s'))  # ș
    comp('latin-ext', 'uni021A', [0x021A], [('T', 0, 0), ('uni0326', 524, 0)], W('T'))  # Ț
    comp('latin-ext', 'uni021B', [0x021B], [('t', 0, 0), ('uni0326', 343, 0)], W('t'))  # ț

    # ---- symbols (Latin file) ---------------------------------------------
    right = union(stroke([(100, M), (1330, M)]), stroke([(900, M + 430), (1330, M), (900, M - 430)]))
    geom('latin', 'arrowright', [0x2192], right, 1620)
    geom('latin', 'arrowleft', [0x2190], mirror(right, 1620), 1620)
    up = union(stroke([(750, 0), (750, 1250)]), stroke([(320, 820), (750, 1250), (1180, 820)]))
    geom('latin', 'arrowup', [0x2191], up, 1500)
    geom('latin', 'arrowdown', [0x2193], mirror_v(up, 1250), 1500)
    geom('latin', 'lessequal', [0x2264], union(shift(L('less'), 0, 200), stroke([(110, B), (990, B)])), W('less'))
    geom('latin', 'greaterequal', [0x2265], union(shift(L('greater'), 0, 200), stroke([(90, B), (970, B)])), W('greater'))
    geom('latin', 'notequal', [0x2260], union(L('equal'), stroke([(380, 180), (830, 1250)])), W('equal'))
    check = stroke([(140, 720), (520, 330), (1320, 1250)])
    geom('latin', 'uni2713', [0x2713, 0x2714], check, 1450)  # ✓ ✔
    return out


def zhe(cx, cy, dx, dy, top):
    """Ж / ж: a stem with four arms at the angle of K's, cut flat at the
    baseline and the top line."""
    k = 1.5
    arms = [stroke([(cx, cy), (cx + sx * dx * k, cy + sy * dy * k)]) for sx in (-1, 1) for sy in (-1, 1)]
    return band(union(stroke([(cx, -10), (cx, top + 10)]), *arms), 0, top)


def mirror_v(g, height):
    import shapely.affinity

    return shapely.affinity.scale(g, 1, -1, origin=(0, height / 2))
