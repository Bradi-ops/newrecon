"""
Endpoint prober — solo GET, pasivo.
Prueba URLs descubiertas + 40+ rutas comunes interesantes.
"""
import asyncio
import logging
from urllib.parse import urljoin, urlparse

import httpx

from recrew.config import config
from recrew.context import ReconContext, EndpointResult

logger = logging.getLogger("recrew.endpoints")

COMMON_PATHS = [
    # API & docs
    "/api/", "/api/v1/", "/api/v2/", "/api/v3/",
    "/graphql", "/graphiql", "/graphql/playground",
    "/swagger", "/swagger-ui", "/swagger-ui.html",
    "/swagger.json", "/swagger.yaml",
    "/openapi.json", "/openapi.yaml",
    "/api-docs", "/api-docs.json", "/redoc", "/docs",
    # Admin
    "/admin", "/admin/", "/administrator", "/dashboard",
    "/wp-admin", "/wp-login.php", "/manager", "/console",
    # Exposure
    "/.env", "/.env.local", "/.env.production", "/.env.dev",
    "/.git/HEAD", "/.git/config",
    "/config.json", "/config.js", "/settings.json",
    "/web.config", "/.htaccess",
    # Discovery
    "/robots.txt", "/sitemap.xml", "/sitemap_index.xml",
    "/crossdomain.xml", "/.well-known/security.txt",
    "/.well-known/openid-configuration",
    # Monitoring
    "/health", "/healthz", "/health/check",
    "/status", "/metrics",
    "/actuator", "/actuator/health", "/actuator/env",
    "/actuator/mappings", "/actuator/beans",
    "/debug", "/info", "/build",
    # Auth endpoints
    "/login", "/logout", "/register", "/signup",
    "/oauth/authorize", "/oauth/token",
    "/auth", "/auth/login",
    "/v1/auth", "/api/auth", "/api/login",
    # Misc
    "/server-status", "/server-info",
    "/version", "/changelog",
]

_INTERESTING_STATUS = {200, 201, 204, 301, 302, 307, 308, 401, 403}
_INTERESTING_KW = [
    "admin", "swagger", "graphql", "api-docs", "openapi",
    "debug", "config", ".env", ".git", "metrics",
    "actuator", "console", "dashboard", "wp-admin",
]
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"


async def probe_endpoints(ctx: ReconContext) -> dict:
    base = (f"{urlparse(ctx.target_url).scheme}://"
            f"{urlparse(ctx.target_url).netloc}")
    all_urls: set[str] = set()

    # Desde el spider
    for page in ctx.pages:
        all_urls.add(page.url)
        for link in page.links:
            all_urls.add(link)

    # Desde análisis JS
    for js in ctx.js_files:
        for ep in js.endpoints + js.api_calls:
            if ep.startswith("http"):
                all_urls.add(ep)
            elif ep.startswith("/"):
                all_urls.add(urljoin(base, ep))

    # Rutas comunes
    for path in COMMON_PATHS:
        all_urls.add(urljoin(base, path))

    logger.info(f"Probing {len(all_urls)} endpoints...")

    headers = {"User-Agent": _UA, **ctx.auth_headers}
    results: list[EndpointResult] = []

    async with httpx.AsyncClient(
        headers=headers, cookies=ctx.auth_cookies,
        follow_redirects=False, verify=False, timeout=10, http2=True,
        limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
    ) as client:
        tasks = [_probe_one(client, url) for url in all_urls]
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        for r in raw:
            if isinstance(r, EndpointResult):
                results.append(r)
                ctx.endpoints.append(r)

    interesting = [r for r in results if r.interesting]
    logger.info(f"Endpoints: {len(results)} probed, "
                f"{len([r for r in results if r.status==200])} OK, "
                f"{len(interesting)} interesting")

    return {
        "total_probed": len(results),
        "accessible_200": len([r for r in results if r.status == 200]),
        "auth_required_401": len([r for r in results if r.status == 401]),
        "forbidden_403": len([r for r in results if r.status == 403]),
        "interesting": len(interesting),
        "endpoints": [
            {"url": r.url, "status": r.status, "redirect": r.redirect,
             "interesting": r.interesting, "server": r.server, "notes": r.notes}
            for r in sorted(results, key=lambda x: (-x.interesting, x.status))
        ],
    }


async def _probe_one(client: httpx.AsyncClient, url: str) -> EndpointResult:
    try:
        resp = await client.get(url)
        notes: list[str] = []
        interesting = resp.status_code in _INTERESTING_STATUS
        path = urlparse(url).path.lower()

        if any(kw in path for kw in _INTERESTING_KW):
            interesting = True
            notes.append("Interesting path")
        if resp.status_code in (401, 403):
            notes.append("Protected — resource exists")
            interesting = True
        if resp.status_code == 200:
            if ".git" in path:
                notes.append("⚠️ .git directory EXPOSED")
                interesting = True
            if ".env" in path:
                notes.append("⚠️ .env file EXPOSED")
                interesting = True
            if "swagger" in path or "openapi" in path:
                notes.append("📖 API docs exposed")
                interesting = True

        server = resp.headers.get("server", "")
        powered = resp.headers.get("x-powered-by", "")
        if server:
            notes.append(f"Server: {server}")
        if powered:
            notes.append(f"X-Powered-By: {powered}")
            interesting = True

        return EndpointResult(
            url=url, status=resp.status_code,
            redirect=resp.headers.get("location"),
            content_type=resp.headers.get("content-type", ""),
            server=server, interesting=interesting,
            notes="; ".join(notes),
        )
    except Exception as e:
        return EndpointResult(
            url=url, status=0, redirect=None, content_type="",
            server="", interesting=False, notes=f"Error: {str(e)[:80]}",
        )