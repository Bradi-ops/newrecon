"""
Spider v4 — httpx async + BeautifulSoup.
Rápido, sin dependencias pesadas, reconocimiento pasivo.
Crawl BFS mismo dominio con deduplicación y progress visible.
"""
import asyncio
import logging
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Comment
from rich.console import Console
from rich.progress import (
    Progress, SpinnerColumn, TextColumn,
    BarColumn, TaskProgressColumn,
)

from recrew.config import config
from recrew.context import ReconContext, PageResult

logger = logging.getLogger("recrew.spider")
console = Console()

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".webp",
    ".pdf", ".zip", ".gz", ".tar", ".exe", ".dmg",
}

_CONCURRENCY = 8  # requests paralelos máximo


async def run_spider(
    ctx: ReconContext,
    max_pages: int = 100,
    max_depth: int = 5,
) -> dict:
    parsed = urlparse(ctx.target_url)
    base_domain = parsed.netloc

    visited: set[str] = set()
    # BFS queue: (url, depth)
    queue: list[tuple[str, int]] = [(ctx.target_url, 0)]

    console.print(
        f"[cyan]🕷️  Spider starting:[/cyan] [bold]{ctx.target_url}[/bold] "
        f"(max {max_pages} pages, depth {max_depth})"
    )

    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        **ctx.auth_headers,
    }

    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        headers=headers,
        cookies=ctx.auth_cookies,
        follow_redirects=True,
        verify=False,
        timeout=httpx.Timeout(15.0, connect=8.0),
        http2=True,
        limits=httpx.Limits(
            max_connections=_CONCURRENCY + 4,
            max_keepalive_connections=_CONCURRENCY,
        ),
    ) as client:

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
                # Tomar batch de URLs del mismo nivel de profundidad
                batch: list[tuple[str, int]] = []
                while queue and len(batch) < _CONCURRENCY:
                    url, depth = queue.pop(0)
                    norm = _norm(url)
                    if norm in visited or depth > max_depth:
                        continue
                    if _should_skip(url):
                        continue
                    visited.add(norm)
                    ctx.all_urls.add(norm)
                    batch.append((url, depth))

                if not batch:
                    break

                # Crawl batch en paralelo
                tasks = [
                    _crawl_one(client, semaphore, url, base_domain)
                    for url, _ in batch
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for (url, depth), result in zip(batch, results):
                    if isinstance(result, Exception):
                        result = PageResult(
                            url=url, status=0, title="",
                            error=str(result)[:120],
                        )

                    ctx.pages.append(result)

                    # Log visible por página
                    sc = result.status
                    color = (
                        "green" if sc == 200 else
                        "yellow" if sc in (301, 302, 307, 308) else
                        "red" if sc >= 400 else
                        "dim"
                    )
                    short = url if len(url) <= 75 else url[:72] + "..."
                    console.print(
                        f"  [[{color}]{sc or 'ERR'}[/{color}]] {short} "
                        f"[dim]("
                        f"{len(result.links)} links · "
                        f"{len(result.forms)} forms · "
                        f"{len(result.scripts)} scripts"
                        f")[/dim]"
                    )

                    # Encolar nuevos links
                    if not result.error and depth < max_depth:
                        added = 0
                        for link in result.links:
                            if _norm(link) not in visited:
                                queue.append((link, depth + 1))
                                added += 1
                        if added:
                            logger.debug(f"  → {added} new URLs queued")

                    short2 = url if len(url) <= 55 else url[:52] + "..."
                    progress.update(task, current_url=short2)
                    progress.advance(task)

                    if len(ctx.pages) >= max_pages:
                        break

    n_pages = len(ctx.pages)
    n_forms = sum(len(p.forms) for p in ctx.pages)
    n_scripts = sum(len(p.scripts) + len(p.inline_scripts) for p in ctx.pages)
    n_comments = sum(len(p.comments) for p in ctx.pages)

    console.print(
        f"\n[green]✅ Spider done:[/green] "
        f"[bold]{n_pages}[/bold] pages · "
        f"[bold]{n_forms}[/bold] forms · "
        f"[bold]{n_scripts}[/bold] scripts · "
        f"[bold]{n_comments}[/bold] comments\n"
    )

    return {
        "pages_crawled": n_pages,
        "urls_discovered": len(ctx.all_urls),
        "total_forms": n_forms,
        "total_scripts": n_scripts,
        "total_comments": n_comments,
        "pages": [
            {
                "url": p.url,
                "status": p.status,
                "title": p.title,
                "forms": len(p.forms),
                "scripts": len(p.scripts),
            }
            for p in ctx.pages
        ],
    }


async def _crawl_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    url: str,
    base_domain: str,
) -> PageResult:
    async with semaphore:
        try:
            resp = await client.get(url)
            status = resp.status_code
            headers = dict(resp.headers)
            ct = headers.get("content-type", "")

            # Solo parsear HTML
            if "html" not in ct and status == 200:
                return PageResult(
                    url=url, status=status, title="",
                    headers=headers, html="",
                )

            html = resp.text
            soup = BeautifulSoup(html, "lxml")

            # Título
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""

            # Links same-domain
            links: list[str] = []
            for a in soup.find_all("a", href=True):
                full = urljoin(url, a["href"])
                p = urlparse(full)
                if p.netloc == base_domain and p.scheme in ("http", "https"):
                    links.append(_norm(full))

            # Scripts externos
            scripts: list[str] = [
                urljoin(url, t["src"])
                for t in soup.find_all("script", src=True)
                if t.get("src")
            ]

            # Scripts inline (>20 chars)
            inline_scripts: list[str] = [
                t.string.strip()
                for t in soup.find_all("script")
                if not t.get("src") and t.string
                and len(t.string.strip()) > 20
            ]

            # Formularios
            forms: list[dict] = []
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

            # Comentarios HTML
            comments: list[str] = [
                str(c).strip()
                for c in soup.find_all(
                    text=lambda t: isinstance(t, Comment)
                )
                if len(str(c).strip()) > 3
            ]

            return PageResult(
                url=url,
                status=status,
                title=title,
                links=list(dict.fromkeys(links)),
                forms=forms,
                scripts=scripts,
                inline_scripts=inline_scripts,
                comments=comments,
                headers=headers,
                html=html,
            )

        except httpx.TimeoutException:
            logger.warning(f"[TIMEOUT] {url}")
            return PageResult(url=url, status=0, title="", error="Timeout")
        except Exception as e:
            logger.warning(f"[ERROR] {url}: {e}")
            return PageResult(url=url, status=0, title="", error=str(e)[:120])


def _norm(url: str) -> str:
    p = urlparse(url)
    return p._replace(fragment="").geturl().rstrip("/")


def _should_skip(url: str) -> bool:
    path = urlparse(url).path.lower().split("?")[0]
    return any(path.endswith(ext) for ext in _SKIP_EXT)
