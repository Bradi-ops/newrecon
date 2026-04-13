"""
Auth tool — form login (Playwright), cookie inject, bearer token.
"""
import json
import logging
from playwright.async_api import async_playwright
from recrew.config import config
from recrew.context import ReconContext

logger = logging.getLogger("recrew.auth")


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

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=config.HEADLESS,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        bctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0",
            ignore_https_errors=True,
        )
        page = await bctx.new_page()
        try:
            await page.goto(config.AUTH_URL, timeout=config.BROWSER_TIMEOUT)

            # Selectores multi-fallback para username y password
            user_sel = ", ".join([
                f'[name="{config.AUTH_USERNAME_FIELD}"]',
                f'#{config.AUTH_USERNAME_FIELD}',
                'input[type="email"]',
                'input[placeholder*="user" i]',
                'input[placeholder*="email" i]',
            ])
            pass_sel = ", ".join([
                f'[name="{config.AUTH_PASSWORD_FIELD}"]',
                f'#{config.AUTH_PASSWORD_FIELD}',
                'input[type="password"]',
            ])

            await page.fill(user_sel, config.AUTH_USERNAME)
            await page.fill(pass_sel, config.AUTH_PASSWORD)

            async with page.expect_navigation(timeout=config.BROWSER_TIMEOUT):
                await page.keyboard.press("Enter")

            await page.wait_for_load_state("networkidle",
                                           timeout=config.BROWSER_TIMEOUT)

            cookies = await bctx.cookies()
            ctx.auth_cookies = {c["name"]: c["value"] for c in cookies}

            final_url = page.url
            likely_failed = any(
                kw in final_url.lower()
                for kw in ["login", "signin", "error", "failed"]
            )

            return {
                "success": not likely_failed,
                "method": "form",
                "final_url": final_url,
                "cookies_captured": len(cookies),
                **({"warning": "Still on login-like URL"} if likely_failed else {}),
            }
        except Exception as e:
            logger.error(f"Form login failed: {e}")
            return {"success": False, "error": str(e)}
        finally:
            await browser.close()