"""QR code generation for equipment assets.

Each asset's QR code encodes the absolute URL of its detail page so that
scanning it jumps straight there (login required). Callers pass the fully-built
URL — in path mode that is tenant-qualified (``/t/<slug>/equipment/<id>``), so
the util itself stays tenant-agnostic.

A community logo, when given, sits solid black on a white tile in the middle of
the code. That covers modules, so those codes switch to the highest error
correction level (H, ~30% recoverable), which is what keeps them scanning.
"""

import base64
from io import BytesIO
from typing import Optional

import qrcode
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_M

# Tested to scan down to 110 px wide; re-test before raising it.
LOGO_FRACTION = 0.24
_BORDER = 4


def asset_qr_png_bytes(url: str) -> bytes:
    """Render a QR code for ``url`` as PNG bytes."""
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def asset_qr_data_uri(url: str) -> str:
    """Render a QR code for ``url`` as a ``data:image/png;base64`` URI for inline display."""
    b64 = base64.b64encode(asset_qr_png_bytes(url)).decode('ascii')
    return f"data:image/png;base64,{b64}"


def asset_qr_svg(url: str, logo_data_uri: Optional[str] = None) -> str:
    """Render a QR code for ``url`` as an SVG document, optionally with a centred logo.

    ``logo_data_uri`` must be a ``data:`` URI the app built from bytes it stored
    itself (``TenantLogoService.qr_logo_data_uri``); it is drawn through a
    colour matrix that keeps only its alpha, so any logo prints solid black.
    """
    code = qrcode.QRCode(
        error_correction=ERROR_CORRECT_H if logo_data_uri else ERROR_CORRECT_M,
        border=_BORDER,
    )
    code.add_data(url)
    code.make(fit=True)
    matrix = code.get_matrix()
    n = len(matrix)
    path = ''.join(
        f'M{x} {y}h1v1h-1z'
        for y, row in enumerate(matrix)
        for x, dark in enumerate(row)
        if dark
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" '
        f'width="{n * 10}" height="{n * 10}" shape-rendering="crispEdges">',
        f'<rect width="{n}" height="{n}" fill="#fff"/>',
        f'<path d="{path}" fill="#000"/>',
    ]
    if logo_data_uri:
        side = (n - 2 * _BORDER) * LOGO_FRACTION
        tile = side + 2
        c = n / 2
        parts += [
            '<filter id="k"><feColorMatrix type="matrix" '
            'values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 0"/></filter>',
            f'<rect x="{c - tile / 2:.3f}" y="{c - tile / 2:.3f}" width="{tile:.3f}" '
            f'height="{tile:.3f}" rx="1" fill="#fff"/>',
            f'<image href="{logo_data_uri}" x="{c - side / 2:.3f}" y="{c - side / 2:.3f}" '
            f'width="{side:.3f}" height="{side:.3f}" filter="url(#k)" '
            'shape-rendering="geometricPrecision"/>',
        ]
    parts.append('</svg>')
    return ''.join(parts)


def asset_qr_svg_data_uri(url: str, logo_data_uri: Optional[str] = None) -> str:
    """``asset_qr_svg`` as a ``data:image/svg+xml;base64`` URI for an ``<img>``."""
    b64 = base64.b64encode(asset_qr_svg(url, logo_data_uri).encode('utf-8')).decode('ascii')
    return f'data:image/svg+xml;base64,{b64}'
