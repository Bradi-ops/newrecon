"""
Playwright async spider.
Renderiza JS (networkidle), extrae links, forms, scripts, comments.
Scope: mismo dominio origen.
"""
import logging
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, Page
from bs4 import BeautifulSoup, Comment

from recrew.config import config
from recrew.context import ReconContext, PageResult

logger = logging.getLogger("recrew.spider")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Extensiones que no vale la pena renderizar (ahorro de tiempo)
_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
             ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".webp", ".pdf"}


async def run_spider(ctx: ReconContext, max_pages: int = 100,
                     max_depth: int = 5) -> dict:
    parsed = urlparse(ctx.target_url)
    base_domain = parsed.netloc
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(ctx.target_url, 0)]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=config.HEADLESS,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"],
        )
        bctx = await browser.new_context(
            user_agent=_UA,
            ignore_https_errors=True,
            extra_http_headers=ctx.auth_headers,
        )

        # Inyectar cookies de auth
        if ctx.auth_cookies:
            await bctx.add_cookies([
                {"name": k, "value": v, "domain": base_domain, "path": "/"}
                for k, v in ctx.auth_cookies.items()
            ])

        page = await bctx.new_page()

        # Bloquear recursos pesados que no aportan al recon
        await page.route(
            "**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf,mp4,mp3,webp,pdf}",
            lambda r: r.abort()
        )

        while queue and len(ctx.pages) < max_pages:
            url, depth = queue.pop(0)
            norm = _norm(url)
            if norm in visited or depth > max_depth:
                continue
            if any(url.lower().endswith(ext) for ext in _SKIP_EXT):
                continue

            visited.add(norm)
            ctx.all_urls.add(norm)

            result = await _crawl_one(page, url, base_domain)
            ctx.pages.append(result)

            if not result.error and depth < max_depth:
                for link in result.links:
                    if _norm(link) not in visited:
                        queue.append((link, depth + 1))

        await browser.close()

    return {
        "pages_crawled": len(ctx.pages),
        "urls_discovered": len(ctx.all_urls),
        "total_forms": sum(len(p.forms) for p in ctx.pages),
        "total_scripts": sum(len(p.scripts) for p in ctx.pages),
        "total_comments": sum(len(p.comments) for p in ctx.pages),
        "pages": [{"url": p.url, "status": p.status, "title": p.title,
                   "forms": len(p.forms), "scripts": len(p.scripts)}
                  for p in ctx.pages],
    }


async def _crawl_one(page: Page, url: str, base_domain: str) -> PageResult:
    try:
        resp = await page.goto(url, wait_until="networkidle",
                               timeout=config.BROWSER_TIMEOUT)
        await page.wait_for_timeout(config.WAIT_AFTER_LOAD_MS)

        status = resp.status if resp else 0
        headers = dict(resp.headers) if resp else {}
        title = await page.title()
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        # Links same-domain
        links = []
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"])
            p = urlparse(full)
            if p.netloc == base_domain and p.scheme in ("http", "https"):
                links.append(_norm(full))

        # External scripts
        scripts = [urljoin(url, t["src"])
                   for t in soup.find_all("script", src=True)]

        # Inline scripts (>20 chars)
        inline_scripts = [
            t.string.strip()
            for t in soup.find_all("script")
            if not t.get("src") and t.string and len(t.string.strip()) > 20
        ]

        # Forms
        forms = []
        for form in soup.find_all("form"):
            forms.append({
                "action": urljoin(url, form.get("action", "")),
                "method": form.get("method", "GET").upper(),
                "fields": [
                    {"name": f.get("name", ""), "type": f.get("type", "text"),
                     "id": f.get("id", ""), "placeholder": f.get("placeholder", "")}
                    for f in form.find_all(["input", "textarea", "select"])
                ],
            })

        # HTML comments (filtrar los vacíos)
        comments = [
            str(c).strip()
            for c in soup.find_all(text=lambda t: isinstance(t, Comment))
            if len(str(c).strip()) > 3
        ]

        logger.debug(f"[{status}] {url} — {len(links)} links, "
                     f"{len(forms)} forms, {len(scripts)} scripts")

        return PageResult(
            url=url, status=status, title=title,
            links=list(dict.fromkeys(links)),
            forms=forms, scripts=scripts,
            inline_scripts=inline_scripts, comments=comments,
            headers=headers, html=html,
        )
    except Exception as e:
        logger.warning(f"Crawl error {url}: {e}")
        return PageResult(url=url, status=0, title="", error=str(e))


def _norm(url: str) -> str:
    p = urlparse(url)
    return p._replace(fragment="").geturl().rstrip("/")