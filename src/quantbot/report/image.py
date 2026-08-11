"""Render an HTML brief to a PNG via a headless Chromium (Playwright).

Playwright is an optional dependency (the `image` extra). If it is not installed, or a
render fails, the caller falls back to the text brief — image delivery is a nicety, not
a hard requirement. Install with:  pip install '.[image]' && playwright install chromium
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


class ImageRenderUnavailable(RuntimeError):
    """Playwright (or its browser) is not available."""


def render_png(
    html: str,
    out_path: str | Path,
    *,
    width: int = 820,
    theme: str = "dark",
    scale: int = 2,
) -> Path:
    """Screenshot a full-page render of `html` to `out_path`. Returns the path.

    `theme` emulates the viewer's color scheme ("dark" | "light") so the image matches
    the intended look regardless of the headless default. `scale` is the device pixel
    ratio — 2 yields a crisp retina image for Telegram.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImageRenderUnavailable(
            "playwright is not installed. Install the image extra: "
            "pip install '.[image]' && playwright install chromium"
        ) from exc

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    color_scheme = "dark" if theme == "dark" else "light"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            try:
                page = browser.new_page(
                    viewport={"width": width, "height": 1400},
                    device_scale_factor=scale,
                    color_scheme=color_scheme,
                )
                page.set_content(html, wait_until="networkidle")
                page.screenshot(path=str(out), full_page=True)
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - surface as our sentinel for the caller
        raise ImageRenderUnavailable(f"headless render failed: {exc}") from exc

    log.info("Rendered brief image -> %s", out)
    return out
