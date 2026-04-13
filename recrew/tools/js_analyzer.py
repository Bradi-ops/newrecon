"""
JS analyzer — descarga JS files y extrae endpoints, API calls, comentarios.
"""
import re
import logging
import httpx
from urllib.parse import urlparse

from recrew.config import config
from recrew.context import ReconContext, JSFile

logger = logging.getLogger("recrew.js")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"

ENDPOINT_PATTERNS = [
    r"""['"` ]([/][a-zA-Z0-9_\-/\.]{2,80})['"` ]""",
    r"""(?:fetch|axios(?:\.get|\.post|\.put|\.delete)?)\s*\(\s*['"` ]([^'"` \n]{3,})""",
    r"""(?:url|endpoint|baseURL|baseUrl|API_URL|apiUrl|apiEndpoint)\s*[:=]\s*['"` ]([^'"` \n]{3,})""",
    r"""(?:GET|POST|PUT|DELETE|PATCH)\s+['"` ]([^'"` \n]{3,})""",
]

COMMENT_RE = re.compile(
    r"//\s*(?:TODO|FIXME|HACK|BUG|NOTE|XXX|DEBUG|PASSWORD|SECRET|KEY|CRED)[^\n]*"
    r"|/\*[\s\S]*?(?:TODO|FIXME|DEBUG|PASSWORD|SECRET|KEY|HACK)[\s\S]*?\*/",
    re.IGNORECASE,
)

_BORING = {"", "/", "//", ".js", ".css", ".png", "http", "https",
           "example.com", "localhost"}


async def analyze_js_files(ctx: ReconContext) -> dict:
    # Collectar URLs únicas de scripts externos del mismo dominio
    base_domain = urlparse(ctx.target_url).netloc
    js_urls: dict[str, None] = {}
    for page in ctx.pages:
        for src in page.scripts:
            if urlparse(src).netloc == base_domain:
                js_urls[src] = None
        # Minar inline scripts directamente
        for inline in page.inline_scripts:
            _mine_inline(ctx, inline, page.url)

    headers = {"User-Agent": _UA, **ctx.auth_headers}
    analyzed: list[dict] = []

    async with httpx.AsyncClient(
        headers=headers, cookies=ctx.auth_cookies,
        follow_redirects=True, verify=False, timeout=15, http2=True,
        limits=httpx.Limits(max_connections=20),
    ) as client:
        for url in js_urls:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                content = resp.text
                endpoints = _extract_endpoints(content)
                comments = COMMENT_RE.findall(content)
                api_calls = _extract_api_calls(content)

                ctx.js_files.append(JSFile(
                    url=url,
                    content=content[:60_000],
                    endpoints=endpoints,
                    api_calls=api_calls,
                    comments=[c.strip() for c in comments[:40]],
                ))
                analyzed.append({
                    "url": url, "size_kb": round(len(content) / 1024, 1),
                    "endpoints": len(endpoints),
                    "api_calls": len(api_calls),
                    "comments": len(comments),
                })
                logger.debug(f"JS {url}: {len(endpoints)} eps, {len(comments)} cmts")
            except Exception as e:
                logger.warning(f"JS fetch error {url}: {e}")

    return {
        "files_analyzed": len(analyzed),
        "total_endpoints": sum(f["endpoints"] for f in analyzed),
        "total_api_calls": sum(f["api_calls"] for f in analyzed),
        "total_comments": sum(f["comments"] for f in analyzed),
        "files": analyzed,
    }


def _mine_inline(ctx: ReconContext, content: str, page_url: str) -> None:
    eps = _extract_endpoints(content)
    cmts = COMMENT_RE.findall(content)
    if eps or cmts:
        ctx.js_files.append(JSFile(
            url=f"inline:{page_url}",
            content=content[:20_000],
            endpoints=eps,
            api_calls=_extract_api_calls(content),
            comments=[c.strip() for c in cmts[:20]],
        ))


def _extract_endpoints(content: str) -> list[str]:
    found: set[str] = set()
    for pat in ENDPOINT_PATTERNS:
        for m in re.finditer(pat, content):
            ep = m.group(1).strip()
            if ep and ep not in _BORING and not ep.startswith("//"):
                found.add(ep)
    return sorted(found)


def _extract_api_calls(content: str) -> list[str]:
    pat = re.compile(
        r"""(?:fetch|axios(?:\.get|\.post|\.put|\.delete|\.patch)?"""
        r"""|http(?:Client)?\.(?:get|post|put|delete))\s*\(\s*['"` ]([^'"` \n]+)""",
        re.IGNORECASE,
    )
    return sorted({m.group(1) for m in pat.finditer(content)})