"""
Auth tool sin Playwright — httpx para todo.
form: POST directo al action del formulario de login.
cookie: inyección directa.
bearer: header Authorization.
"""
import json
import logging

import httpx
from bs4 import BeautifulSoup

from recrew.config import config
from recrew.context import ReconContext

logger = logging.getLogger("recrew.auth")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 Chrome/124.0")


async def authenticate(ctx: ReconContext) -> dict:
    method = config.AUTH_TYPE.lower()
    logger.info(f"Auth method: {method}")
    if method == "cookie":
        return _inject_cookies(ctx)
    elif method == "bearer":
        return _inject_bearer(ctx)
    elif method == "form":
        return await _form_login(ctx)
    return {"success": False, "error": f"Unknown AUTH_TYPE: {method}"}


def _inject_cookies(ctx: ReconContext) -> dict:
    try:
        cookies = json.loads(config.AUTH_COOKIES)
        ctx.auth_cookies = {str(k): str(v) for k, v in cookies.items()}
        return {"success": True, "method": "cookie", "count": len(cookies)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _inject_bearer(ctx: ReconContext) -> dict:
    if not config.AUTH_BEARER_TOKEN:
        return {"success": False, "error": "AUTH_BEARER_TOKEN not set"}
    ctx.auth_headers["Authorization"] = f"Bearer {config.AUTH_BEARER_TOKEN}"
    return {"success": True, "method": "bearer"}


async def _form_login(ctx: ReconContext) -> dict:
    if not all([config.AUTH_URL, config.AUTH_USERNAME, config.AUTH_PASSWORD]):
        return {"success": False, "error": "AUTH_URL / USERNAME / PASSWORD missing"}

    async with httpx.AsyncClient(
        headers={"User-Agent": _UA},
        follow_redirects=True,
        verify=False,
        timeout=15,
    ) as client:
        try:
            # 1. GET la página de login para obtener CSRF token si lo hay
            login_page = await client.get(config.AUTH_URL)
            soup = BeautifulSoup(login_page.text, "lxml")

            # Extraer campos hidden (CSRF, etc.)
            form = soup.find("form")
            payload: dict = {}
            if form:
                for inp in form.find_all("input", type="hidden"):
                    name = inp.get("name", "")
                    value = inp.get("value", "")
                    if name:
                        payload[name] = value
                action = urljoin_safe(config.AUTH_URL, form.get("action", ""))
            else:
                action = config.AUTH_URL

            # Credenciales
            payload[config.AUTH_USERNAME_FIELD] = config.AUTH_USERNAME
            payload[config.AUTH_PASSWORD_FIELD] = config.AUTH_PASSWORD

            # 2. POST login
            resp = await client.post(action, data=payload)

            # Guardar cookies de sesión
            ctx.auth_cookies = dict(client.cookies)

            final_url = str(resp.url)
            likely_failed = any(
                kw in final_url.lower()
                for kw in ["login", "signin", "error", "failed"]
            )

            return {
                "success": not likely_failed,
                "method": "form_httpx",
                "final_url": final_url,
                "status": resp.status_code,
                "cookies_captured": len(ctx.auth_cookies),
                **({"warning": "Still on login-like URL"} if likely_failed else {}),
            }
        except Exception as e:
            logger.error(f"Form login failed: {e}")
            return {"success": False, "error": str(e)}


def urljoin_safe(base: str, path: str) -> str:
    if not path or path == "#":
        return base
    from urllib.parse import urljoin
    return urljoin(base, path)
