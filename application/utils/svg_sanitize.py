"""Allowlist sanitiser for uploaded SVG logos.

An SVG is an XML document that can run script, load remote resources and embed
HTML. Rather than hunting for the bad parts, this rebuilds the document from an
allowlist of drawing elements and presentation attributes, so anything it does
not recognise (``script``, ``foreignObject``, ``image``, ``a``, animation,
editor metadata, every ``on*`` handler) is gone. References may only point
inside the document (``#id``), and CSS is refused if it could reach outside it.

The output is what gets stored and served; the upload as sent never is.
"""

import re
import xml.etree.ElementTree as ET
from typing import Optional

SVG_NS = 'http://www.w3.org/2000/svg'
_XLINK_HREF = '{http://www.w3.org/1999/xlink}href'

MAX_ELEMENTS = 20_000
MAX_DEPTH = 64

_ELEMENTS = frozenset({
    'svg', 'g', 'defs', 'title', 'desc', 'style', 'symbol', 'use',
    'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
    'linearGradient', 'radialGradient', 'stop', 'clipPath', 'mask', 'pattern',
    'text', 'tspan',
})
_TEXT_ELEMENTS = frozenset({'style', 'title', 'desc', 'text', 'tspan'})

_ATTRS = frozenset({
    'id', 'class', 'style', 'viewBox', 'preserveAspectRatio', 'width', 'height', 'version',
    'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'fx', 'fy', 'fr',
    'dx', 'dy', 'd', 'points', 'transform', 'href', 'offset',
    'fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width', 'stroke-linecap',
    'stroke-linejoin', 'stroke-miterlimit', 'stroke-dasharray', 'stroke-dashoffset',
    'stroke-opacity', 'opacity', 'color', 'display', 'visibility', 'paint-order',
    'vector-effect', 'isolation', 'mix-blend-mode',
    'clip-path', 'clip-rule', 'clipPathUnits', 'mask', 'maskUnits', 'maskContentUnits',
    'stop-color', 'stop-opacity', 'gradientUnits', 'gradientTransform', 'spreadMethod',
    'patternUnits', 'patternContentUnits', 'patternTransform',
    'font-family', 'font-size', 'font-weight', 'font-style', 'text-anchor',
    'dominant-baseline', 'letter-spacing',
})

_LOCAL_URL = re.compile(r'url\(\s*["\']?#[A-Za-z_][\w.:-]*["\']?\s*\)', re.I)
_CSS_FORBIDDEN = re.compile(r'@import|@font-face|@namespace|expression\s*\(|javascript:|\\|behavior\s*:|-moz-binding', re.I)
_NUM = re.compile(r'^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$')

_NOT_SVG = "That file isn't an SVG image."


def looks_like_svg(data: bytes) -> bool:
    """True when ``data`` is XML whose root is meant to be an ``<svg>``."""
    head = data[:4096].lstrip(b'\xef\xbb\xbf \t\r\n').lower()
    return head.startswith(b'<') and b'<svg' in head


def _safe_css(text: str) -> bool:
    return not _CSS_FORBIDDEN.search(text) and 'url(' not in _LOCAL_URL.sub('', text.lower())


def _safe_value(name: str, value: str) -> bool:
    if name == 'href':
        return value.startswith('#')
    if name == 'style':
        return _safe_css(value)
    return 'url(' not in _LOCAL_URL.sub('', value.lower()) and 'javascript:' not in value.lower()


def _local(tag: str) -> Optional[str]:
    """The SVG local name of ``tag``, or ``None`` for any other namespace."""
    if tag.startswith('{' + SVG_NS + '}'):
        return tag[len(SVG_NS) + 2:]
    return None


def _copy(src: ET.Element, depth: int, count: list[int]) -> Optional[ET.Element]:
    name = _local(src.tag) if isinstance(src.tag, str) else None
    if name not in _ELEMENTS:
        return None
    count[0] += 1
    if count[0] > MAX_ELEMENTS or depth > MAX_DEPTH:
        raise ValueError('That SVG is too complex. Try simplifying it or exporting a PNG.')
    out = ET.Element(f'{{{SVG_NS}}}{name}')
    for key, value in src.attrib.items():
        attr = 'href' if key == _XLINK_HREF else key
        if attr in _ATTRS and _safe_value(attr, value):
            out.set(attr, value)
    if name in _TEXT_ELEMENTS and src.text:
        if name == 'style' and not _safe_css(src.text):
            raise ValueError('That SVG has styles that load outside files. Remove them and try again.')
        out.text = src.text
    for child in src:
        copied = _copy(child, depth + 1, count)
        if copied is not None:
            out.append(copied)
            if name in _TEXT_ELEMENTS and child.tail:
                copied.tail = child.tail
    return out


def sanitise_svg(data: bytes) -> bytes:
    """Return a cleaned copy of ``data`` that keeps only drawing content.

    Raises ``ValueError`` with a message fit to show the uploader.
    """
    lowered = data.lower()
    # Refuse entity declarations: that's how XML bombs and external-file reads start.
    if b'<!entity' in lowered or (b'<!doctype' in lowered and b'[' in lowered.split(b'<svg', 1)[0]):
        raise ValueError('That SVG declares XML entities, which logos never need. Export it again without them.')
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ValueError(_NOT_SVG) from None
    if _local(root.tag) != 'svg':
        raise ValueError(_NOT_SVG)
    clean = _copy(root, 0, [0])
    assert clean is not None
    if 'viewBox' not in clean.attrib:
        w = _NUM.match(clean.get('width', ''))
        h = _NUM.match(clean.get('height', ''))
        if not (w and h):
            raise ValueError('That SVG has no size. Export it with a viewBox and try again.')
        clean.set('viewBox', f'0 0 {w.group(1)} {h.group(1)}')
    if not any(_local(el.tag) not in {'svg', 'title', 'desc', 'defs', 'style'} for el in clean.iter()):
        raise ValueError('That SVG has nothing to draw.')
    ET.register_namespace('', SVG_NS)
    return ET.tostring(clean, encoding='utf-8', xml_declaration=False)
