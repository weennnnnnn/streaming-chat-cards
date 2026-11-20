"""SVG placeholder "image" generation.

The brief asks for a fake image-generation step: after 5-10 seconds the
backend returns a deterministic placeholder derived from the prompt — a set
of colored blocks with the prompt text rendered on them. Pure SVG, no
external service.
"""

from __future__ import annotations

import hashlib
import html


def generate_placeholder_svg(prompt: str) -> str:
    """Deterministic SVG placeholder seeded by the prompt text."""
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()

    # Pick palette + layout parameters from the digest so the same prompt
    # always renders the same "image" (nice for demoing determinism).
    hues = [digest[i] * 360 // 255 for i in range(5)]
    width, height = 480, 320
    safe = html.escape(prompt, quote=True)
    wrapped = _wrap(safe, 28)

    blocks = []
    for i, hue in enumerate(hues):
        bw = width // 5
        x = i * bw
        h = height * (0.35 + digest[5 + i] / 255 * 0.6)
        y = height - h
        blocks.append(
            f'<rect x="{x}" y="{y:.0f}" width="{bw - 6}" height="{h:.0f}" '
            f'rx="8" fill="hsl({hue}, 65%, 55%)" opacity="0.9"/>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'font-family="sans-serif">'
        f'<rect width="{width}" height="{height}" fill="#111827"/>'
        + "".join(blocks)
        + f'<text x="{width // 2}" y="34" fill="#f9fafb" font-size="15" '
        f'text-anchor="middle">{wrapped[0]}</text>'
        f'<text x="{width // 2}" y="56" fill="#9ca3af" font-size="12" '
        f'text-anchor="middle">placeholder generated from prompt sha256</text>'
        f"</svg>"
    )


def _wrap(text: str, max_len: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_len and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
        if len(lines) >= 2:
            break
    if cur and len(lines) < 2:
        lines.append(cur)
    return lines + [""] * (2 - len(lines))
