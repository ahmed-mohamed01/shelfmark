"""Generated placeholder covers for Elscione releases, in BookOrbit's Bauhaus style.

The index carries no cover images, so a release row that is not the requested
volume (or whose book has no cover_url) would otherwise render the frontend's
grey "No Cover" box. placeholder_cover() fills ``extra["preview"]`` with a
small SVG data URL instead, ported from BookOrbit's TypeScript so the two stay
in sync: the same fnv1a hash, the same mulberry32 rng, the same palette
grouping (a series shares its colour scheme and ground, its volumes differ in
composition) and the same micro-tier composition (a monogram over the numeral
or sun motif).
"""

import functools
import math
import re
import urllib.parse
from collections.abc import Callable
from typing import NamedTuple

_W = 400
_H = 600
# Inter's cap height is about 0.727 em; one ratio stands in for the font
# metrics engine BookOrbit has (its metrics()/measure()), because a data URL
# cannot ship font-dependent measurements.
_CAP = 0.72
_MASK32 = 0xFFFFFFFF
_FNV_PRIME = 16777619
_M32 = 0x6D2B79F5
_SANS = "Inter, system-ui, -apple-system, Segoe UI, Helvetica, Arial, sans-serif"
# \w is letters, digits and underscore, so [\W_] is everything else: the same
# set BookOrbit's [^\p{L}\p{N}] rejects.
_NON_ALNUM = re.compile(r"[\W_]+")

# Hand-picked three-colour sets: primary, a lighter partner and a deep third.
_SCHEMES: tuple[tuple[int, int, int], ...] = (
    (27, 255, 90),
    (255, 88, 25),
    (90, 250, 20),
    (45, 200, 262),
    (195, 30, 85),
    (150, 350, 262),
    (295, 85, 190),
    (350, 150, 250),
    (265, 50, 160),
    (115, 15, 240),
    (15, 230, 75),
    (175, 330, 90),
)

Random = Callable[[], float]
# x0, y0, x1, y1 of the motif's box.
Rect = tuple[float, float, float, float]


def _utf16_units(s: str) -> list[int]:
    """The string's UTF-16 code units, what JS ``s.charCodeAt(i)`` iterates.

    Encoding to UTF-16-LE gives astral characters as surrogate pairs, so a
    non-BMP code point hashes as its two units, exactly like JS strings.
    """
    data = s.encode("utf-16-le")
    return [data[i] | (data[i + 1] << 8) for i in range(0, len(data), 2)]


def fnv1a(s: str) -> int:
    """BookOrbit's ``hash``: FNV-1a over UTF-16 code units, unsigned 32-bit.

    JS works on int32 and ends with ``>>> 0``, but XOR and multiply both act
    on raw bits, so keeping ``h`` unsigned in Python yields the same value
    without any sign juggling.
    """
    h = 2166136261
    for unit in _utf16_units(s):
        h ^= unit
        h = (h * _FNV_PRIME) & _MASK32
    return h


def mulberry32(seed: int) -> Random:
    """BookOrbit's ``rng``: mulberry32 with exact JS 32-bit semantics.

    ``Math.imul``, ``|0`` and ``>>>`` are all bit operations on 32-bit values
    (an unsigned shift of a negative int32 has the bits our unsigned int has),
    so masking to 32 bits after each step reproduces the JS sequence exactly.
    """
    a = seed & _MASK32

    def draw() -> float:
        nonlocal a
        a = (a + _M32) & _MASK32
        t = ((a ^ (a >> 15)) * (1 | a)) & _MASK32
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & _MASK32)) & _MASK32) ^ t
        return (t ^ (t >> 14)) / 4294967296

    return draw


def _group_key(s: str) -> str:
    """BookOrbit's ``groupKey``: lowercase, non-alphanumerics as single spaces."""
    return _NON_ALNUM.sub(" ", s.lower()).strip()


def _weighted[T](r: Random, choices: tuple[tuple[T, float], ...]) -> T:
    """BookOrbit's ``weighted``: a fixed mix drawn with one generator call."""
    roll = r() * sum(weight for _value, weight in choices)
    for value, weight in choices:
        roll -= weight
        if roll < 0:
            return value
    return choices[-1][0]


def _ok(l: float, c: float, h: float) -> str:
    """BookOrbit's ``ok``: an oklch() string formatted exactly as it formats them.

    Every hue here is a whole number, so JS's Math.round is plain int(); and
    Python's modulo already answers non-negative, like the JS double-mod.
    """
    return f"oklch({l:.3f} {c:.3f} {int(h) % 360})"


class _Colors(NamedTuple):
    bg: str
    fg: str
    fg2: str
    c1: str
    c2: str
    c3: str


def _colors(s: Random, group: int) -> _Colors:
    """The Bauhaus palette for one series, ported from BookOrbit's ``colors``.

    The scheme comes from the group hash and the ground from the group's rng,
    so every volume of a series shares the same background while its ink and
    accents stay legible on it.
    """
    h, h2, h3 = _SCHEMES[group % len(_SCHEMES)]
    yellowish = 70 < h < 115
    ground = _weighted(s, (("paper", 40.0), ("ink", 35.0), ("color", 25.0)))
    c1 = _ok(0.8 if yellowish else 0.62, 0.16 if yellowish else 0.19, h)
    c2 = _ok(0.8, 0.14, h2)
    c3 = _ok(0.46, 0.14, h3)
    paper = _ok(0.955, 0.014, 85)
    ink = _ok(0.19, 0.014, h)
    if ground == "paper":
        return _Colors(bg=paper, fg=ink, fg2=_ok(0.43, 0.02, h), c1=c1, c2=c2, c3=c3)
    if ground == "ink":
        return _Colors(bg=ink, fg=paper, fg2=_ok(0.76, 0.02, h), c1=c1, c2=c2, c3=c3)
    # A colour ground keeps the ink as its third colour and swaps the primary
    # for the paper the numeral can sit on (BookOrbit's "color" branch).
    return _Colors(
        bg=c1,
        fg=ink if yellowish else paper,
        fg2=_ok(0.3, 0.05, h) if yellowish else _ok(0.93, 0.03, h),
        c1=ink if yellowish else paper,
        c2=c2,
        c3=ink,
    )


def _r2(x: float) -> str:
    """A coordinate the way JS ``Math.round(x * 100) / 100`` prints it.

    JS rounds halves away from zero; Python's round() is half-even, so the
    floor(x + 0.5) form keeps the two byte-identical.
    """
    value = math.floor(x * 100 + 0.5) / 100
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _num(v: float) -> str:
    """A volume number without leading zeros: 3.0 -> "3", 1.5 -> "1.5".

    Never raises for any float: index parsing already treats a non-finite
    number (an absurd digit run) as no volume, but this formatter must not
    explode the whole result mapping on whatever floats still reach it, so
    int(inf) is never attempted and non-finite values render as their repr.
    """
    if not math.isfinite(v):
        return repr(v)
    if v == int(v):
        return str(int(v))
    return f"{v:.10f}".rstrip("0").rstrip(".")


def _volume_text(volume: tuple[float, float] | None) -> str:
    """The numeral to draw: the low end, "low-high" for a range, "" unnumbered."""
    if volume is None:
        return ""
    low, high = volume
    return _num(low) if low == high else f"{_num(low)}-{_num(high)}"


def _xml_escape(s: str) -> str:
    """BookOrbit's ``xmlEscape``: the monogram and volume land in XML text nodes."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _monogram(title: str) -> str:
    """BookOrbit's ``monogram``: first letter of the first significant word.

    A leading article ("The", "A", "An") is skipped when more words follow, so
    "The Spice and Wolf" is marked with "S", not "T".
    """
    words = title.split()
    if not words:
        return "?"
    sig = next(
        (
            word
            for i, word in enumerate(words)
            if not (i == 0 and word.lower() in ("the", "a", "an") and len(words) > 1)
        ),
        words[0],
    )
    stripped = re.sub(r"^[\W_]+", "", sig)
    return (stripped[:1] or "?").upper()


def _measure(text: str) -> float:
    """Approximate per-em text width: 0.62 per digit, 0.35 for '-' and '.'.

    BookOrbit measures with a font metrics engine; the index entries only ever
    draw digits with '-' and '.', so a per-glyph ratio is exact enough here.
    """
    return sum(0.35 if ch in "-." else 0.62 for ch in text)


def _numeral_motif(rect: Rect, col: _Colors, r: Random, text: str) -> str:
    """BookOrbit's ``numeral`` motif: the volume number bleeding off the bottom.

    The digits fit the motif's box: at most 1.08 cap heights tall and 98% of
    its width wide, ending past the right edge by design.
    """
    x0, y0, x1, y1 = rect
    w = x1 - x0
    hgt = y1 - y0
    size = min(hgt * 1.08 / _CAP, w * 0.98 / (_measure(text) or 1.0))
    cx = x0 + w * (0.18 + r() * 0.12)
    return (
        f'<circle cx="{_r2(cx)}" cy="{_r2(y0 + hgt * 0.42)}" r="{_r2(min(hgt, w) * 0.36)}"'
        f' fill="{col.c2}"/>'
        f'<text x="{_r2(x1 + size * 0.04)}" y="{_r2(y1 + size * 0.04)}" font-size="{_r2(size)}"'
        f' font-weight="900" letter-spacing="-0.06em" text-anchor="end" fill="{col.c1}">'
        f"{_xml_escape(text)}</text>"
    )


def _sun_motif(rect: Rect, col: _Colors, r: Random, canvas_w: float) -> str:
    """BookOrbit's ``sun`` motif: a striped setting sun for unnumbered files."""
    x0, y0, x1, y1 = rect
    w = x1 - x0
    hgt = y1 - y0
    rad = min(hgt * 0.95, w * 0.44)
    cx = x0 + w * (0.3 + r() * 0.4)
    s = f'<circle cx="{_r2(cx)}" cy="{_r2(y1)}" r="{_r2(rad)}" fill="{col.c1}"/>'
    for i in range(4):
        s += (
            f'<rect x="{_r2(x0)}" y="{_r2(y1 - rad * (0.12 + i * 0.16))}"'
            f' width="{_r2(w)}" height="{_r2(4 + i * 3.5)}" fill="{col.bg}"/>'
        )
    # The small companion circle sits opposite the sun's centre.
    mr = rad * 0.2
    mx = x0 + w * 0.16 if cx > canvas_w / 2 else x1 - w * 0.16
    s += f'<circle cx="{_r2(mx)}" cy="{_r2(y0 + max(mr + 8, hgt * 0.26))}" r="{_r2(mr)}" fill="{col.c2}"/>'
    return s


def _placeholder_svg(series: str, title: str, volume: tuple[float, float] | None, seed: str) -> str:
    """One deterministic Bauhaus cover as an SVG document.

    Nothing is clipped: the motif bleeds off the bottom edge on purpose and
    ``preserveAspectRatio="xMidYMid slice"`` crops to the viewBox, the same way
    BookOrbit's covers bleed.
    """
    series_key = _group_key(series)
    title_key = _group_key(title)
    # The group is keyed by the series (as in BookOrbit), so a series reads as
    # a set; an index entry always has a folder, the title-key fallback just
    # keeps the hash total.
    group = fnv1a(f"series:{series_key}") if series_key else fnv1a(f"title:{title_key or seed}")
    # The item mixes the volume text and the entry path in, so the volumes of
    # one series differ while their palette stays shared.
    volume_text = _volume_text(volume)
    item = fnv1a(f"{title_key}|{volume_text}|{seed}")
    s = mulberry32(group)
    r = mulberry32(item)
    col = _colors(s, group)

    motif_rect: Rect = (0.0, _H * 0.42, _W, _H)
    if volume_text:
        art = _numeral_motif(motif_rect, col, r, volume_text)
    else:
        art = _sun_motif(motif_rect, col, r, _W)

    # The monogram: half the cover wide, its first cap 8% down the cover.
    fs = _W * 0.5
    mark = (
        f'<text x="30" y="{_r2(_H * 0.08 + _CAP * fs)}" font-size="{_r2(fs)}"'
        f' font-weight="800" fill="{col.fg}">{_xml_escape(_monogram(title))}</text>'
    )
    body = f'<rect width="{_W}" height="{_H}" fill="{col.bg}"/>' + art + mark
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}"'
        f' viewBox="0 0 {_W} {_H}" preserveAspectRatio="xMidYMid slice"'
        f' font-family="{_SANS}">' + body + "</svg>"
    )


@functools.lru_cache(maxsize=4096)
def placeholder_cover(
    series: str, title: str, volume: tuple[float, float] | None, seed: str
) -> str:
    """A generated Bauhaus-style cover for one index entry, as an SVG data URL.

    Mirrors BookOrbit's default placeholder covers, keyed the same way so the
    two stay visually in sync. Deterministic: the same entry always produces
    the same bytes, so rows do not flicker between renders. Cached, because a
    search maps every hit through this function. The whole URL stays a few
    hundred bytes, far under the 3 KB cap the modal's payload budget allows.
    """
    svg = _placeholder_svg(series, title, volume, seed)
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg, safe="")
