"""
ReconCrew v4 — Entry point.
Configura el LLM client, construye el grafo de agentes,
lanza el Orchestrator contra el target.
"""
import asyncio
import logging
import sys

from openai import AsyncOpenAI
from agents import Runner, set_default_openai_client, set_default_openai_api
from rich.console import Console
from rich.panel import Panel

from recrew.config import config
from recrew.context import ReconContext

console = Console()
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("recrew")


def setup_llm() -> str:
    """Configura el cliente OpenAI Agents SDK según LLM_PROVIDER.
    Devuelve el nombre de modelo a usar."""
    if config.LLM_PROVIDER == "lmstudio":
        client = AsyncOpenAI(
            base_url=config.LM_STUDIO_URL,
            api_key="lm-studio",
        )
        set_default_openai_client(client, use_for_tracing=False)
        set_default_openai_api("chat_completions")
        logger.info(f"LLM: LM Studio @ {config.LM_STUDIO_URL} — {config.LM_STUDIO_MODEL}")
        return config.LM_STUDIO_MODEL

    elif config.LLM_PROVIDER == "anthropic":
        client = AsyncOpenAI(
            base_url="https://api.anthropic.com/v1",
            api_key=config.ANTHROPIC_API_KEY,
            default_headers={"anthropic-version": "2023-06-01"},
        )
        set_default_openai_client(client, use_for_tracing=False)
        set_default_openai_api("chat_completions")
        logger.info(f"LLM: Anthropic — {config.MODEL_NAME}")
        return config.MODEL_NAME

    else:  # openai (default)
        client = AsyncOpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
        )
        set_default_openai_client(client)
        logger.info(f"LLM: OpenAI — {config.MODEL_NAME}")
        return config.MODEL_NAME


async def run_recon(target_url: str) -> None:
    model = setup_llm()

    # Importar DESPUÉS de setup_llm() para que el client global esté listo
    from recrew.agents.orchestrator import build_orchestrator

    ctx = ReconContext(target_url=target_url)

    console.print(Panel.fit(
        f"[bold green]🕵️  ReconCrew v4[/bold green]\n"
        f"Target : [cyan]{target_url}[/cyan]\n"
        f"Model  : [yellow]{model}[/yellow]\n"
        f"Auth   : [magenta]{config.AUTH_ENABLED} ({config.AUTH_TYPE})[/magenta]\n"
        f"Pages  : [white]{config.MAX_PAGES}[/white] max | "
        f"Depth: [white]{config.MAX_DEPTH}[/white]",
        title="Starting Recon Mission",
    ))

    orchestrator = build_orchestrator(model)

    initial_prompt = (
        f"BEGIN RECON MISSION\n"
        f"Target URL: {target_url}\n"
        f"Auth enabled: {config.AUTH_ENABLED} (type: {config.AUTH_TYPE})\n\n"
        f"Execute the FULL reconnaissance workflow in order:\n"
        + (f"1. Call authenticate — {config.AUTH_TYPE} login needed.\n"
           if config.AUTH_ENABLED else
           f"1. Skip authenticate — auth not configured.\n")
        + f"2. Call crawl_target — spider the site with Playwright JS rendering.\n"
          f"3. Call analyze_javascript — mine JS files for endpoints and API calls.\n"
          f"4. Call scan_secrets — regex scan all HTML and JS for sensitive data.\n"
          f"5. Call probe_endpoints — passive GET probe all discovered URLs.\n"
          f"6. Call generate_report — produce the HTML report.\n"
          f"Write a concise executive summary when all steps are complete."
    )

    result = await Runner.run(
        starting_agent=orchestrator,
        input=initial_prompt,
        context=ctx,
    )

    # ← NUEVO: guardar el summary para incluirlo en el report
    ctx.agent_summary = result.final_output

    console.print(Panel(
        result.final_output,
        title="[bold green]✅ Recon Complete",
        border_style="green",
    ))
    if ctx.report_path:
        console.print(f"\n[bold]📊 Report:[/bold] [cyan]{ctx.report_path}[/cyan]")


def main() -> None:
    target = config.TARGET_URL or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not target:
        console.print("[red]Usage:[/red] python -m recrew.main <target_url>")
        sys.exit(1)
    asyncio.run(run_recon(target))


if __name__ == "__main__":
    main()
