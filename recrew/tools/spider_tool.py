"""
Playwright async spider — v4 fixed.
Estrategia de wait robusta: domcontentloaded + networkidle opcional (con timeout corto).
Logging visible + Rich progress bar.
"""
import logging
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout
from bs4 import BeautifulSoup, Comment
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from recrew.config import config
from recrew.context import ReconContext, PageResult

logger = logging.getLogger("recrew.spider")
console = Console()

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
             ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".webp", ".pdf", ".zip"}


async def run_spider(ctx: ReconContext, max_pages: int = 100,
                     max_depth: int = 5) -> dict:
    parsed = urlparse(ctx.target_url)
    base_domain = parsed.netloc
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(ctx.target_url, 0)]

    console.print(f"[cyan]🕷️  Spider starting:[/cyan] [bold]{ctx.target_url}[/bold] "
                  f"(max {max_pages} pages, depth {max_depth})")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=config.HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-background-networking",   # ← reduce background requests
                "--disable-background-timer-throttling",
            ],
        )
        bctx = await browser.new_context(
            user_agent=_UA,
            ignore_https_errors=True,
            extra_http_headers=ctx.auth_headers,
        )

        # Auth cookies
        if ctx.auth_cookies:
            await bctx.add_cookies([
                {"name": k, "value": v, "domain": base_domain, "path": "/"}
                for k, v in ctx.auth_cookies.items()
            ])

        page = await bctx.new_page()

        # Bloquear recursos pesados + tracking pixels para reducir requests background
        await page.route(
            "**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf,mp4,mp3,webp,pdf,zip}",
            lambda r: r.abort()
        )
        # Bloquear dominios de analytics comunes (reduce background requests infinitos)
        async def _block_analytics(route):
            url = route.request.url
            blocked = any(d in url for d in [
                "google-analytics.com", "googletagmanager.com",
                "facebook.com/tr", "connect.facebook.net",
                "analytics.tiktok.com", "bat.bing.com",
                "hotjar.com", "clarity.ms",
                "doubleclick.net", "googlesyndication.com",
            ])
            if blocked:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", _block_analytics)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("[cyan]{task.fields[current_url]}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                "[green]Crawling...",
                total=max_pages,
                current_url="starting...",
            )

            while queue and len(ctx.pages) < max_pages:
                url, depth = queue.pop(0)
                norm = _norm(url)

                if norm in visited or depth > max_depth:
                    continue
                if any(url.lower().split("?")[0].endswith(ext) for ext in _SKIP_EXT):
                    continue

                visited.add(norm)
                ctx.all_urls.add(norm)

                short_url = url if len(url) <= 60 else url[:57] + "..."
                progress.update(task, current_url=short_url)

                result = await _crawl_one(page, url, base_domain)
                ctx.pages.append(result)

                status_color = ("green" if result.status == 200 else
                                "yellow" if result.status in (301, 302, 307) else
                                "red" if result.status >= 400 else "white")
                console.print(
                    f"  [[{status_color}]{result.status}[/{status_color}]] "
                    f"{url[:80]} "
                    f"[dim]({len(result.links)} links, "
                    f"{len(result.forms)} forms, "
                    f"{len(result.scripts)} scripts)[/dim]"
                )

                if not result.error and depth < max_depth:
                    new_links = 0
                    for link in result.links:
                        if _norm(link) not in visited:
                            queue.append((link, depth + 1))
                            new_links += 1
                    if new_links:
                        console.print(f"    [dim]→ {new_links} new URLs queued[/dim]")

                progress.advance(task)

        await browser.close()

    total_forms = sum(len(p.forms) for p in ctx.pages)
    total_scripts = sum(len(p.scripts) + len(p.inline_scripts) for p in ctx.pages)
    total_comments = sum(len(p.comments) for p in ctx.pages)

    console.print(
        f"\n[green]✅ Spider done:[/green] "
        f"[bold]{len(ctx.pages)}[/bold] pages | "
        f"[bold]{total_forms}[/bold] forms | "
        f"[bold]{total_scripts}[/bold] scripts | "
        f"[bold]{total_comments}[/bold] comments\n"
    )

    return {
        "pages_crawled": len(ctx.pages),
        "urls_discovered": len(ctx.all_urls),
        "total_forms": total_forms,
        "total_scripts": total_scripts,
        "total_comments": total_comments,
        "pages": [
            {"url": p.url, "status": p.status, "title": p.title,
             "forms": len(p.forms), "scripts": len(p.scripts)}
            for p in ctx.pages
        ],
    }


async def _crawl_one(page: Page, url: str, base_domain: str) -> PageResult:
    try:
        # ── ESTRATEGIA DE WAIT ROBUSTA ──────────────────────────────────────
        # 1. goto con domcontentloaded (rápido, siempre funciona)
        # 2. Intentar networkidle con timeout corto (para SPAs que terminan rápido)
        # 3. wait_for_timeout fijo para que el JS del SPA renderice
        # Así no nos quedamos colgados en sites con tracking infinito
        # ────────────────────────────────────────────────────────────────────
        resp = await page.goto(
            url,
            wait_until="domcontentloaded",   # ← CAMBIADO: no espera networkidle
            timeout=config.BROWSER_TIMEOUT,
        )

        # Intentar networkidle con timeout corto (5s) — no bloqueante
        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=5_000,  # 5 segundos máximo
            )
        except PWTimeout:
            # Normal en SPAs con analytics — continuamos igualmente
            logger.debug(f"networkidle timeout (expected) for {url}")

        # Espera fija para que el JS del SPA renderice el DOM
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
                    {
                        "name": f.get("name", ""),
                        "type": f.get("type", "text"),
                        "id": f.get("id", ""),
                        "placeholder": f.get("placeholder", ""),
                    }
                    for f in form.find_all(["input", "textarea", "select"])
                ],
            })

        # HTML comments
        comments = [
            str(c).strip()
            for c in soup.find_all(text=lambda t: isinstance(t, Comment))
            if len(str(c).strip()) > 3
        ]

        return PageResult(
            url=url, status=status, title=title,
            links=list(dict.fromkeys(links)),
            forms=forms, scripts=scripts,
            inline_scripts=inline_scripts, comments=comments,
            headers=headers, html=html,
        )

    except PWTimeout:
        logger.warning(f"[TIMEOUT] {url}")
        return PageResult(url=url, status=0, title="", error="Timeout")
    except Exception as e:
        logger.warning(f"[ERROR] {url}: {e}")
        return PageResult(url=url, status=0, title="", error=str(e))


def _norm(url: str) -> str:
    p = urlparse(url)
    return p._replace(fragment="").geturl().rstrip("/")
