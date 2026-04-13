from agents import Agent, function_tool, RunContextWrapper
from recrew.context import ReconContext
from recrew.tools.spider_tool import run_spider
import json


def build_spider_agent(model: str) -> Agent:
    @function_tool
    async def run_playwright_spider(
        ctx: RunContextWrapper[ReconContext],
        max_pages: int = 100,
        max_depth: int = 5,
    ) -> str:
        """Crawl the target with Playwright (full JS rendering).
        Extracts pages, links, forms, scripts, and HTML comments.
        Works on React/Angular/Vue SPAs."""
        result = await run_spider(ctx.context, max_pages=max_pages,
                                  max_depth=max_depth)
        return json.dumps(result, indent=2)

    return Agent(
        name="SpiderAgent",
        instructions=(
            "You are SpiderAgent. Call run_playwright_spider to crawl the target. "
            "Summarize: pages found, forms, scripts, and any immediately interesting "
            "paths (admin, api, login, etc.)."
        ),
        model=model,
        tools=[run_playwright_spider],
    )