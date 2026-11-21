"""Impression hors ligne du même rapport HTML, avec ses captures et ses liens."""

from pathlib import Path
from tempfile import TemporaryDirectory

PRINT_CSS = """
@page { size: A4; margin: 15mm; }
@media print {
  :root {
    --bg: #fff; --panel: #f4f6f9; --ink: #1a2536; --dim: #465166;
    --accent: #365d98; --good: #176345; --warn: #805615; --bad: #96322b;
  }
  body { margin: 0; padding: 0; font-size: 10pt; line-height: 1.4; }
  main { max-width: none; }
  h1,h2,h3 { color: var(--ink); break-after: avoid; }
  h2 { margin-block-start: 7mm; }
  a { color: #254e82; overflow-wrap: anywhere; }
  .badge.good { background: #e1f1e8; }
  .badge.warn { background: #f7edd8; }
  .badge.bad { background: #fae5e2; }
  .finding { border-radius: 0; }
  article:has(img) { break-inside: avoid; }
  img { display: block; max-height: 110mm; object-fit: contain; }
  pre, td, li, h3 { overflow-wrap: anywhere; }
  tr { break-inside: avoid; }
  th, td { padding: 2mm; border-color: #d5dce7; }
  summary { font-weight: 600; }
}
"""


def export_pdf(html_path: Path) -> Path:
    """Exporte un run existant sans relancer sa campagne."""
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as exc:
        raise RuntimeError('Installez le moteur PDF avec pip install "testmyapp[web]"') from exc

    source = html_path.resolve().as_uri()
    destination = html_path.with_suffix(".pdf")
    try:
        with sync_playwright() as playwright, TemporaryDirectory(dir=html_path.parent) as tmp:
            pending = Path(tmp) / destination.name
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(java_script_enabled=False, service_workers="block")
                page.set_default_timeout(15000)
                page.route("**/*", lambda route: route.continue_() if route.request.url == source else route.abort())
                page.goto(source, wait_until="load")
                page.evaluate("""async css => {
                  const style = document.createElement("style");
                  style.textContent = css;
                  document.head.append(style);
                  document.querySelectorAll("details").forEach(item => item.open = true);
                  const images = [...document.querySelectorAll('img[src^="data:"]')];
                  for (const image of images) image.loading = "eager";
                  let timer;
                  try {
                    await Promise.race([
                      Promise.all(images.map(image => image.decode())),
                      new Promise((_, reject) => { timer = setTimeout(() => reject(Error("captures non chargées")), 15000); })
                    ]);
                  } finally {
                    clearTimeout(timer);
                  }
                }""", PRINT_CSS)
                page.pdf(path=pending, print_background=True, prefer_css_page_size=True, tagged=True, outline=True)
            finally:
                browser.close()
            pending.replace(destination)
    except Error as exc:
        raise RuntimeError(f"Export PDF impossible. Vérifiez Chromium avec python -m playwright install chromium. {exc}") from exc
    return destination
