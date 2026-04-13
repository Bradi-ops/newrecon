from agents import Agent, function_tool, RunContextWrapper
from recrew.context import ReconContext
from recrew.tools.js_analyzer import analyze_js_files
import json


def build_js_agent(model: str) -> Agent:
    @function_tool
    async def analyze_js_files_tool(ctx: RunContextWrapper[ReconContext]) -> str:
        """Download and analyze JavaScript files from ctx.pages.scripts.
        Extracts API endpoints, fetch/axios calls, and interesting comments."""
        result = await analyze_js_files(ctx.context)
        return json.dumps(result, indent=2)

    return Agent(
        name="JSAgent",
        instructions=(
            "You are JSAgent. Call analyze_js_files_tool. Summarize endpoints found, "
            "API call patterns, and any interesting comments (TODOs, credentials, "
            "debug info, internal paths)."
        ),
        model=model,
        tools=[analyze_js_files_tool],
    )