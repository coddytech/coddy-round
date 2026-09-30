"""Build Coddy Round, the rounded Audiowide the Coddy wordmark is drawn in.

The logo in public/icons/logo-text.svg was made in Figma from Audiowide by
rounding its letters. Measured against the font, the rule is exact: the weight
and every curve are Audiowide's own, and each SHARP OUTER corner is filleted
with a radius of half the stem (130 of 2048 units), so a square stroke end
becomes a semicircle. Inner (concave) corners and existing curves are left
alone. This script applies that rule to every glyph, so running text set in
Coddy Round matches the wordmark.

What it builds, per weight (400 Regular to 800 ExtraBold):

- Coddy Round itself: Audiowide's two Google Fonts subsets merged into one
  master (glyphs, cmap and kerning), plus what Audiowide never had
  (additions.py): a Cyrillic alphabet drawn from Audiowide's own parts and
  stroke, and a few symbols. Each weight is drawn once (weights.py) and
  then written twice: complete, as the family's TTF and WOFF2 in fonts/, and
  split by unicode-range (latin, latin-ext, cyrillic) for coddy.tech.
- Hebrew and Arabic companions for coddy.tech only: Varela Round Hebrew and
  Noto Sans Arabic brought to Coddy Round's stroke weight and put through the
  same rounding, served under the same CSS family by unicode-range.

For coddy.tech it writes content-hashed files into public/fonts (they are
cached immutable for a year, so a changed font must be a new URL), the
@font-face block between the coddy-round:start/end markers in
src/styles/globals.css, and src/util/coddyRoundFaces.json, which the app
reads to preload exactly the files a piece of text needs. Never edit any of
those by hand.

Sources (sources/upstream/): the Audiowide Google Fonts subsets, Varela Round
Hebrew and Noto Sans Arabic, all SIL Open Font License 1.1. Audiowide's
Reserved Font Name "Audiowide" is why the family has a new name. OFL.txt
carries every original copyright notice and the full license, next to the
fonts both here and in public/fonts.

Run: ./build.sh   (or python3 sources/build.py; needs requirements.txt)
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import shapely
from fontTools import subset
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.ttLib.tables import ttProgram
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphComponent

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import additions  # noqa: E402
import geometry as G  # noqa: E402
import weights  # noqa: E402

PKG = os.path.dirname(HERE)
ROOT = os.path.abspath(os.path.join(PKG, '..', '..'))
UPSTREAM = os.path.join(HERE, 'upstream')
WEB_DIR = os.path.join(ROOT, 'public', 'fonts')
CSS = os.path.join(ROOT, 'src', 'styles', 'globals.css')
MANIFEST = os.path.join(ROOT, 'src', 'util', 'coddyRoundFaces.json')
# Inside the coddy.tech repository the build also writes the site's web fonts,
# CSS and manifest; as a standalone font repository it writes fonts/ only.
IN_SITE = os.path.exists(CSS)

FAMILY = 'Coddy Round'
VERSION = '2.001'
YEAR = 2026
# The project's public home: the upstream repository (Google Fonts wants its
# git URL in the copyright line). Published there with publish.sh.
PROJECT_URL = 'https://github.com/coddytech/coddy-round'
COPYRIGHT = f'Copyright {YEAR} The Coddy Round Project Authors ({PROJECT_URL})'
LICENSE_DESCRIPTION = (
    'This Font Software is licensed under the SIL Open Font License, Version 1.1. '
    'This license is available with a FAQ at: https://openfontlicense.org'
)
LICENSE_URL = 'https://openfontlicense.org'
# head.created/modified: fixed, so an unchanged font hashes to the same file.
TIMESTAMP = 3_870_000_000  # 2026-08-19, seconds since 1904

# Stem width per weight, in 2048ths of an em. 260 is Audiowide's own.
WEIGHTS = {
    400: ('Regular', 260),
    500: ('Medium', 300),
    600: ('SemiBold', 340),
    700: ('Bold', 380),
    800: ('ExtraBold', 420),
}
AUDIOWIDE_STEM = 260
# Horizontal strokes grow by this share of the vertical growth.
CONTRAST = 0.5
# A fitted curve may not stray further than this (font units, 1/2048 em)
# from the outline it replaces; the build stops rather than ship one.
MAX_FIT_DEVIATION = 4
# Heights that stay put in every weight (descender, Cyrillic tails,
# baseline, x-height, cap height, ascender).
ZONES = [-469, -300, 0, 1081, 1434, 1538]

LATIN = (
    'U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, '
    'U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2190-2193, '
    'U+2212, U+2215, U+2260, U+2264-2265, U+2713-2714, U+FEFF, U+FFFD'
)
LATIN_EXT = (
    'U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, '
    'U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, '
    'U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF'
)
CYRILLIC = 'U+0401, U+0404, U+0406-0407, U+0410-044F, U+0451, U+0454, U+0456-0457, U+2116'
HEBREW = 'U+0590-05FF, U+20AA, U+FB1D-FB4F'
ARABIC = (
    'U+0600-06FF, U+0750-077F, U+0870-088E, U+0890-0891, U+0897-08E1, '
    'U+08E3-08FF, U+FB50-FDFF, U+FE70-FE74, U+FE76-FEFC'
)

# The web split of Coddy Round proper, in @font-face order.
WEB_PARTS = {'latin': LATIN, 'latin-ext': LATIN_EXT, 'cyrillic': CYRILLIC}

# Companion faces: another script's font brought to Coddy Round's stem.
# `stem` is the source's own at 2048 units per em.
COMPANIONS = {
    'hebrew': dict(src='varela-round-hebrew.woff2', ranges=HEBREW, stem=187,
                   widen=True, min_gap=50, label='Hebrew', based_on='Varela Round Hebrew'),
    # Arabic joins across glyph edges, so its advances never change: the
    # thicker strokes overlap their neighbours instead of leaving gaps.
    'arabic': dict(src='noto-sans-arabic-regular.woff2', ranges=ARABIC, stem=207,
                   widen=False, min_gap=40, label='Arabic', based_on='Noto Sans Arabic'),
}


def parse_ranges(text):
    out = []
    for part in text.split(','):
        a, _, b = part.strip()[2:].partition('-')
        out.append((int(a, 16), int(b or a, 16)))
    return out


def codepoints(text):
    return {u for a, b in parse_ranges(text) for u in range(a, b + 1)}


# ------------------------------------------------------------ glyph tables


def ttglyph(contours):
    pen = TTGlyphPen(None)
    G.draw(pen, contours)
    return pen.glyph()


def composite(parts):
    g = Glyph()
    g.numberOfContours = -1
    g.components = []
    for base, dx, dy in parts:
        c = GlyphComponent()
        c.glyphName, c.x, c.y, c.flags = base, round(dx), round(dy), 0x4
        g.components.append(c)
    return g


def add_glyph(font, name, glyph, advance, unicodes=()):
    order = font.getGlyphOrder()
    if name not in order:
        order = order + [name]
        font.setGlyphOrder(order)
        font['glyf'].glyphOrder = order
    font['glyf'].glyphs[name] = glyph
    font['hmtx'].metrics[name] = (advance, 0)
    for table in font['cmap'].tables:
        if table.isUnicode():
            for u in unicodes:
                if table.format != 4 or u <= 0xFFFF:
                    table.cmap[u] = name


def strip_hinting(font):
    # The TrueType instructions address points by index; the outlines have new
    # points, so the old programs would distort them. Browsers render unhinted
    # outlines well, and gasp asks for smoothing at every size.
    for tag in ('fpgm', 'prep', 'cvt ', 'hdmx', 'LTSH', 'VDMX'):
        if tag in font:
            del font[tag]
    if 'gasp' in font:
        font['gasp'].gaspRange = {0xFFFF: 0x000A}
    maxp = font['maxp']
    for attr in ('maxZones', 'maxTwilightPoints', 'maxStorage', 'maxFunctionDefs',
                 'maxInstructionDefs', 'maxStackElements', 'maxSizeOfInstructions'):
        setattr(maxp, attr, 1 if attr == 'maxZones' else 0)
    glyf = font['glyf']
    for name in font.getGlyphOrder():
        g = glyf[name]
        g.program = ttProgram.Program()
        g.program.fromBytecode(b'')


def kerning_pairs(font):
    """Every kerning pair of a font's GPOS as {(left, right): x-advance}."""
    out = {}
    if 'GPOS' not in font:
        return out
    order = font.getGlyphOrder()
    for lookup in font['GPOS'].table.LookupList.Lookup:
        if lookup.LookupType != 2:
            continue
        for st in lookup.SubTable:
            if st.Format == 1:
                for left, pairs in zip(st.Coverage.glyphs, st.PairSet):
                    for rec in pairs.PairValueRecord:
                        v = getattr(rec.Value1, 'XAdvance', 0) if rec.Value1 else 0
                        out.setdefault((left, rec.SecondGlyph), v)
            else:
                c1, c2 = st.ClassDef1.classDefs, st.ClassDef2.classDefs
                members = defaultdict(list)
                for g, c in c2.items():
                    members[c].append(g)
                members[0] = [g for g in order if g not in c2]
                for left in st.Coverage.glyphs:
                    for ci, rec in enumerate(st.Class1Record[c1.get(left, 0)].Class2Record):
                        v = getattr(rec.Value1, 'XAdvance', 0) if rec.Value1 else 0
                        if v:
                            for right in members[ci]:
                                out.setdefault((left, right), v)
    return out


def set_kerning(font, pairs):
    if 'GPOS' in font:
        del font['GPOS']
    have = set(font.getGlyphOrder())
    lines = [f'    pos {a} {b} {v};' for (a, b), v in sorted(pairs.items())
             if v and a in have and b in have]
    fea = ('languagesystem DFLT dflt;\nlanguagesystem latn dflt;\nlanguagesystem cyrl dflt;\n'
           'feature kern {\n' + '\n'.join(lines) + '\n} kern;\n')
    addOpenTypeFeaturesFromString(font, fea, tables=['GPOS'])


# ----------------------------------------------------------------- sources


def master():
    """Audiowide's latin and latin-ext subsets as one font (the glyphs they
    share are identical), with Coddy Round's additions registered. Returns
    the font, the constructed outlines, and the kerning to apply once the
    glyphs have their final widths."""
    lat = TTFont(os.path.join(UPSTREAM, 'audiowide-latin.woff2'))
    ext = TTFont(os.path.join(UPSTREAM, 'audiowide-latin-ext.woff2'))
    lat_gs, ext_gs = lat.getGlyphSet(), ext.getGlyphSet()
    items = additions.build(lat_gs, ext_gs)

    pairs = kerning_pairs(lat)
    for k, v in kerning_pairs(ext).items():
        pairs.setdefault(k, v)

    font = lat
    have = set(font.getGlyphOrder())
    ext_cmap = ext.getBestCmap()
    by_name = defaultdict(list)
    for u, n in ext_cmap.items():
        by_name[n].append(u)
    for name in ext.getGlyphOrder():
        if name not in have:
            add_glyph(font, name, ext['glyf'][name], ext['hmtx'][name][0], by_name[name])
    for u, n in ext_cmap.items():  # shared glyphs mapped only in ext
        for table in font['cmap'].tables:
            if table.isUnicode() and u not in table.cmap and (table.format != 4 or u <= 0xFFFF):
                table.cmap[u] = n

    added = {}
    aliases = defaultdict(list)
    for _, name, unis, spec in items:
        if spec[0] == 'comp':
            add_glyph(font, name, composite(spec[1]), spec[2], unis)
            # A letter that is its Latin twin (А = A) kerns like it.
            if len(spec[1]) == 1 and spec[1][0][1:] == (0, 0):
                aliases[spec[1][0][0]].append(name)
        elif name not in have:  # e.g. the breve copy the split Cyrillic file needed
            add_glyph(font, name, ttglyph([]), spec[2], unis)
            added[name] = spec[1]
    for (a, b), v in list(pairs.items()):
        for a2 in [a, *aliases[a]]:
            for b2 in [b, *aliases[b]]:
                pairs.setdefault((a2, b2), v)
    strip_hinting(font)
    return font, added, pairs


def companion(cfg):
    font = TTFont(os.path.join(UPSTREAM, cfg['src']))
    if font['head'].unitsPerEm != 2048:
        scale_upem(font, 2048)
    strip_hinting(font)
    return font


# -------------------------------------------------------------- the build


def draw_weight(font, added, pool, weight, source_stem, contrast, zones, widen, min_gap,
                exact_regular):
    """Round (and for heavier weights, grow) every outline of `font`."""
    stem = WEIGHTS[weight][1]
    dx = (stem - source_stem) / 2
    dy = dx * contrast
    radius = stem / 2
    shift_x = dx if widen else 0
    glyf, hmtx = font['glyf'], font['hmtx']
    gs = font.getGlyphSet()
    tasks = []
    for name in font.getGlyphOrder():
        g = glyf[name]
        if g.isComposite():
            continue
        geom = added.get(name)
        if geom is None and g.numberOfContours <= 0:
            continue
        if geom is None and exact_regular and dx == 0:
            # Regular Audiowide: round the original curves directly, exactly.
            coords, ends, flags = g.getCoordinates(glyf)
            contours, start = [], 0
            for end in ends:
                contours.append(G.fillet_contour(G.contour_segments(coords, flags, start, end), radius))
                start = end + 1
            glyf[name] = ttglyph(contours)
            continue
        if geom is None:
            geom = G.glyph_geometry(gs, name)
        tasks.append((name, shapely.to_wkb(geom), dx, dy, zones, shift_x, radius, min_gap))
    strays = []
    for name, contours, deviation in pool.map(weights.work, tasks, chunksize=8):
        glyf[name] = ttglyph(contours)
        if deviation > MAX_FIT_DEVIATION:
            strays.append(f'{name} ({deviation:.1f})')
    if strays:
        raise SystemExit(f'weight {weight}: fitted curves stray from the outline: {", ".join(strays)}')
    if widen and dx:
        for name in font.getGlyphOrder():
            adv, lsb = hmtx.metrics[name]
            g = glyf[name]
            if adv > 0 and (g.isComposite() or g.numberOfContours > 0):
                hmtx.metrics[name] = (round(adv + 2 * dx), lsb)
    for name in font.getGlyphOrder():
        g = glyf[name]
        g.recalcBounds(glyf)
        hmtx.metrics[name] = (hmtx.metrics[name][0], getattr(g, 'xMin', 0) if g.numberOfContours else 0)


# Name ID 9: who drew what each face is built from.
DESIGNERS = {
    None: 'Astigmatic, Brian J. Bonislawsky (Audiowide)',
    'Varela Round Hebrew': 'The Varela Round Project Authors (Varela Round Hebrew)',
    'Noto Sans Arabic': 'The Noto Project Authors (Noto Sans Arabic)',
}


def name_font(font, weight, label=None, based_on=None):
    """Names and flags as Google Fonts specifies them for a static family:
    RIBBI styles (Regular, Bold) under the family name, the others under
    'Family Style' with typographic names 16/17, the copyright line in the
    Project Authors form, the standard OFL description, fsType 0."""
    style, _ = WEIGHTS[weight]
    name = font['name']
    original = name.getDebugName(0)
    family = f'{FAMILY} {label}' if label else FAMILY
    ribbi = weight in (400, 700)
    ps = f"{family.replace(' ', '')}-{style}"
    name.names = []
    copyright_ = COPYRIGHT if not based_on else f'{original}. Modifications: {COPYRIGHT}'
    values = {
        0: copyright_,
        1: family if ribbi else f'{family} {style}',
        2: style if ribbi else 'Regular',
        3: f'{VERSION};CODY;{ps}',
        4: f'{family} {style}',
        5: f'Version {VERSION}',
        6: ps,
        8: 'Coddy',
        9: f'{DESIGNERS[based_on]}; Coddy',
        11: 'https://coddy.tech',
        13: LICENSE_DESCRIPTION,
        14: LICENSE_URL,
    }
    if not ribbi:
        values[16], values[17] = family, style
    for nid, text in values.items():
        name.setName(text, nid, 3, 1, 0x409)
    os2 = font['OS/2']
    os2.version = max(os2.version, 4)  # USE_TYPO_METRICS needs version 4
    os2.achVendID = 'CODY'
    os2.usWeightClass = weight
    os2.fsType = 0
    sel = os2.fsSelection & ~0b1100001  # clear italic, bold, regular
    sel |= 0b100000 if weight == 700 else 0
    sel |= 0b1000000 if weight == 400 else 0
    sel |= 0b10000000  # USE_TYPO_METRICS: typo = hhea = win already
    os2.fsSelection = sel
    head = font['head']
    head.macStyle = 1 if weight == 700 else 0
    head.fontRevision = float(VERSION)
    head.created = head.modified = TIMESTAMP
    font.recalcTimestamp = False


def to_bytes(font, flavor=None):
    font.flavor = flavor
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def subset_bytes(font_bytes, unicodes):
    font = TTFont(io.BytesIO(font_bytes))
    font.recalcTimestamp = False
    opts = subset.Options()
    opts.layout_features = ['*']
    opts.name_IDs = ['*']
    opts.name_languages = ['*']
    opts.notdef_outline = True
    opts.hinting = False
    opts.recalc_timestamp = False
    wanted = unicodes & set(font.getBestCmap())
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=wanted)
    sub.subset(font)
    return to_bytes(font, 'woff2'), len(wanted)


def write_web(weight, part, data, manifest):
    """public/fonts/coddy-round-{weight}-{part}.{hash}.woff2, replacing any
    older build of the same face."""
    if not IN_SITE:
        return f'{part} (not written: no site)'
    digest = hashlib.sha1(data).hexdigest()[:8]
    fname = f'coddy-round-{weight}-{part}.{digest}.woff2'
    prefix = f'coddy-round-{weight}-{part}.'
    for old in os.listdir(WEB_DIR):
        if old.startswith(prefix) and old != fname:
            os.remove(os.path.join(WEB_DIR, old))
    with open(os.path.join(WEB_DIR, fname), 'wb') as f:
        f.write(data)
    manifest.setdefault(part, {})[str(weight)] = f'/fonts/{fname}'
    return fname


def build(pool, manifest):
    os.makedirs(os.path.join(PKG, 'fonts', 'ttf'), exist_ok=True)
    os.makedirs(os.path.join(PKG, 'fonts', 'webfonts'), exist_ok=True)
    for weight, (style, _) in WEIGHTS.items():
        font, added, pairs = master()
        draw_weight(font, added, pool, weight, AUDIOWIDE_STEM, CONTRAST, ZONES,
                    widen=True, min_gap=weights.MIN_GAP, exact_regular=True)
        set_kerning(font, pairs)
        name_font(font, weight)
        ttf = to_bytes(font)
        with open(os.path.join(PKG, 'fonts', 'ttf', f'CoddyRound-{style}.ttf'), 'wb') as f:
            f.write(ttf)
        with open(os.path.join(PKG, 'fonts', 'webfonts', f'CoddyRound-{style}.woff2'), 'wb') as f:
            f.write(to_bytes(TTFont(io.BytesIO(ttf)), 'woff2'))
        if not IN_SITE:
            print(f'fonts/ttf/CoddyRound-{style}.ttf')
            continue
        for part, ranges in WEB_PARTS.items():
            data, n = subset_bytes(ttf, codepoints(ranges))
            print(f'{write_web(weight, part, data, manifest)}: {n} chars, {len(data)} bytes')
        for part, cfg in COMPANIONS.items():
            font = companion(cfg)
            draw_weight(font, {}, pool, weight, cfg['stem'], 1.0, None,
                        widen=cfg['widen'], min_gap=cfg['min_gap'], exact_regular=False)
            name_font(font, weight, cfg['label'], cfg['based_on'])
            data, n = subset_bytes(to_bytes(font), codepoints(cfg['ranges']))
            print(f'{write_web(weight, part, data, manifest)}: {n} chars, {len(data)} bytes')


# ----------------------------------------------------------------- license


def write_license():
    """OFL.txt: our copyright line first (Google Fonts requires it to match
    name ID 0 exactly), then the notice of every font it is built from, then
    the full license, as OFL condition 2 asks of any copy.

    Two files, because two things are distributed. The family (OFL.txt here,
    the public repository, the press ZIP) is built from Audiowide alone. The
    site's copy (public/fonts/coddy-round-OFL.txt) also covers the Hebrew and
    Arabic companion faces, which only coddy.tech serves."""
    def notice(fname, use):
        text = TTFont(os.path.join(UPSTREAM, fname))['name'].getDebugName(0)
        # Audiowide's own notice has a stray carriage return inside "Reserved
        # Font Name"; it split the line, and GitHub's license detection then
        # read half a copyright notice as license text and gave up on OFL.
        text = ' '.join(text.split())
        return f'{text} ({use})'

    body = open(os.path.join(HERE, 'ofl-body.txt')).read()
    tail = (
        '\n\nThis Font Software is licensed under the SIL Open Font License, Version 1.1.\n'
        'This license is copied below, and is also available with a FAQ at:\n'
        'https://openfontlicense.org\n\n\n'
    ) + body
    family = [COPYRIGHT, notice('audiowide-latin.woff2', 'Audiowide, the design Coddy Round is rounded from')]
    with open(os.path.join(PKG, 'OFL.txt'), 'w') as f:
        f.write('\n'.join(family) + tail)
    if IN_SITE:
        site = family + [
            notice('varela-round-hebrew.woff2', 'Varela Round, the Hebrew companion face on coddy.tech'),
            notice('noto-sans-arabic-regular.woff2', 'Noto Sans Arabic, the Arabic companion face on coddy.tech'),
        ]
        with open(os.path.join(WEB_DIR, 'coddy-round-OFL.txt'), 'w') as f:
            f.write('\n'.join(site) + tail)


def write_press_zip():
    """public/images/press/coddy-round.zip, the family as the press kit
    offers it (/press, Typeface): every weight as TTF with OFL.txt beside
    them, as the license asks of any copy. Fixed timestamps, so an unchanged
    family zips to the same bytes."""
    out = os.path.join(ROOT, 'public', 'images', 'press', 'coddy-round.zip')
    stamp = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for style, _ in WEIGHTS.values():
            name = f'CoddyRound-{style}.ttf'
            info = zipfile.ZipInfo(f'Coddy Round/{name}', stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, open(os.path.join(PKG, 'fonts', 'ttf', name), 'rb').read())
        info = zipfile.ZipInfo('Coddy Round/OFL.txt', stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, open(os.path.join(PKG, 'OFL.txt'), 'rb').read())
    return out


# --------------------------------------------------------------------- CSS

FALLBACK_SAMPLE = 'The quick brown fox jumps over the lazy dog. Learn to code for free, together.'


def _avg_width(font):
    cmap, hmtx = font.getBestCmap(), font['hmtx']
    widths = [hmtx[cmap[ord(c)]][0] for c in FALLBACK_SAMPLE if ord(c) in cmap]
    return sum(widths) / len(widths) / font['head'].unitsPerEm


def fallback_faces(previous):
    """'Coddy Round Fallback': the local Arial, resized to Coddy Round's
    average width and given its ascent and descent, so text laid out in the
    fallback while the web font loads takes the same space and nothing moves
    when it swaps in (no layout shift on the homepage headline)."""
    arial = {400: '/System/Library/Fonts/Supplemental/Arial.ttf',
             700: '/System/Library/Fonts/Supplemental/Arial Bold.ttf'}
    if not all(os.path.exists(p) for p in arial.values()):
        return previous  # measured on a Mac; the numbers do not change
    out = {}
    for weight, path in arial.items():
        cr = TTFont(os.path.join(PKG, 'fonts', 'ttf', f'CoddyRound-{WEIGHTS[weight][0]}.ttf'))
        size = _avg_width(cr) / _avg_width(TTFont(path))
        upm = cr['head'].unitsPerEm
        out[str(weight)] = {
            'sizeAdjust': round(size * 100, 2),
            'ascentOverride': round(cr['hhea'].ascent / upm / size * 100, 2),
            'descentOverride': round(-cr['hhea'].descent / upm / size * 100, 2),
        }
    return out


def css(manifest, fallback):
    lines = [
        '/* coddy-round:start - generated by scripts/coddy-round/sources/build.py,',
        '   do not edit. Coddy Round: Audiowide with every sharp outer corner rounded,',
        '   the rule the wordmark in /icons/logo-text.svg was drawn with, so display',
        '   text matches the logo. Latin, Latin-Ext and Cyrillic are Coddy Round',
        '   proper; Hebrew and Arabic are companion faces brought to the same stroke',
        '   weight. unicode-range keeps every visitor to the files their text needs;',
        '   file names carry a content hash because /fonts is cached immutable.',
        '   License: /fonts/coddy-round-OFL.txt. Use var(--font-display). */',
        ':root {',
        "    --font-display: 'Coddy Round', 'Coddy Round Fallback', sans-serif;",
        '}',
    ]
    ranges = {**WEB_PARTS, **{k: v['ranges'] for k, v in COMPANIONS.items()}}
    for part, files in manifest.items():
        for weight in WEIGHTS:
            lines.append(
                '@font-face {\n'
                f"    font-family: '{FAMILY}';\n"
                '    font-style: normal;\n'
                f'    font-weight: {weight};\n'
                '    font-display: swap;\n'
                f"    src: url({files[str(weight)]}) format('woff2');\n"
                f'    unicode-range: {ranges[part]};\n'
                '}'
            )
    # The wordmark (components/Wordmark) as a face of its own: the same file
    # as Regular latin (one download), but font-display: block, so the logo
    # shows nothing rather than a stand-in font for the moment before it loads.
    lines.append(
        '@font-face {\n'
        f"    font-family: '{FAMILY} Logo';\n"
        '    font-style: normal;\n'
        '    font-weight: 400;\n'
        '    font-display: block;\n'
        f"    src: url({manifest['latin']['400']}) format('woff2');\n"
        '    unicode-range: U+0043, U+0064, U+006F, U+0079;\n'
        '}'
    )
    # Measured against Arial; Helvetica (Apple) and Roboto (Android) are
    # within a few percent of it, close enough that the swap stays still.
    for weight, (lo, hi), local in (
        ('400', (100, 549), "local('Arial'), local('ArialMT'), local('Helvetica'), local('Roboto')"),
        ('700', (550, 900), "local('Arial Bold'), local('Arial-BoldMT'), local('Helvetica Bold'), local('Roboto Bold')"),
    ):
        m = fallback[weight]
        lines.append(
            '@font-face {\n'
            f"    font-family: '{FAMILY} Fallback';\n"
            f'    font-weight: {lo} {hi};\n'
            f'    src: {local};\n'
            f"    size-adjust: {m['sizeAdjust']}%;\n"
            f"    ascent-override: {m['ascentOverride']}%;\n"
            f"    descent-override: {m['descentOverride']}%;\n"
            '    line-gap-override: 0%;\n'
            '}'
        )
    lines.append('/* coddy-round:end */')
    return '\n'.join(lines)


def write_css(manifest, fallback):
    text = open(CSS).read()
    if 'coddy-round:start' not in text:
        raise SystemExit('globals.css has no coddy-round:start/end markers')
    new = re.sub(r'/\* coddy-round:start.*?/\* coddy-round:end \*/', lambda _: css(manifest, fallback),
                 text, flags=re.S)
    with open(CSS, 'w') as f:
        f.write(new)
    # The repo's own formatter, so a rebuild never leaves a style diff.
    subprocess.run(['npx', 'prettier', '--write', CSS], cwd=ROOT, check=True, capture_output=True)


def main():
    previous = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {}
    files = {}
    with ProcessPoolExecutor() as pool:
        build(pool, files)
    write_license()
    if not IN_SITE:
        return
    fallback = fallback_faces(previous.get('fallback'))
    ranges = {**WEB_PARTS, **{k: v['ranges'] for k, v in COMPANIONS.items()}}
    manifest = {
        'family': FAMILY,
        'version': VERSION,
        'license': '/fonts/coddy-round-OFL.txt',
        'fallback': fallback,
        'faces': [
            {'part': part, 'ranges': parse_ranges(ranges[part]), 'files': files[part]}
            for part in files
        ],
    }
    with open(MANIFEST, 'w') as f:
        json.dump(manifest, f, indent=4)
        f.write('\n')
    subprocess.run(['npx', 'prettier', '--write', MANIFEST], cwd=ROOT, check=True, capture_output=True)
    write_css(files, fallback)
    write_press_zip()


if __name__ == '__main__':
    main()
