from agents import Agent, function_tool, RunContextWrapper
from recrew.context import ReconContext
from recrew.report.generator import generate_report
import json


def build_reporter_agent(model: str) -> Agent:
    @function_tool
    def build_html_report(ctx: RunContextWrapper[ReconContext]) -> str:
        """Generate the final interactive HTML report with ALL raw recon data.
        Returns the path where the report was saved."""
        path = generate_report(ctx.context)
        ctx.context.report_path = path
        return json.dumps({
            "success": True,
            "report_path": path,
            "stats": {
                "pages": len(ctx.context.pages),
                "endpoints": len(ctx.context.endpoints),
                "secrets": len(ctx.context.secrets),
                "js_files": len(ctx.context.js_files),
                "forms": sum(len(p.forms) for p in ctx.context.pages),
                "interesting_endpoints": len(
                    [e for e in ctx.context.endpoints if e.interesting]
                ),
            },
        }, indent=2)

    return Agent(
        name="ReporterAgent",
        instructions=(
            "You are ReporterAgent. Call build_html_report. "
            "Confirm the report path and summarize its contents."
        ),
        model=model,
        tools=[build_html_report],
    )