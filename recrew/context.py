from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PageResult:
    url: str
    status: int
    title: str
    links: list[str] = field(default_factory=list)
    forms: list[dict] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    inline_scripts: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    html: str = ""
    error: Optional[str] = None


@dataclass
class JSFile:
    url: str
    content: str = ""
    endpoints: list[str] = field(default_factory=list)
    api_calls: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class Secret:
    type: str
    value: str
    source_url: str
    context_snippet: str


@dataclass
class EndpointResult:
    url: str
    status: int
    redirect: Optional[str]
    content_type: str
    server: str
    interesting: bool
    notes: str


@dataclass
class ReconContext:
    target_url: str
    pages: list[PageResult] = field(default_factory=list)
    js_files: list[JSFile] = field(default_factory=list)
    secrets: list[Secret] = field(default_factory=list)
    endpoints: list[EndpointResult] = field(default_factory=list)
    all_urls: set[str] = field(default_factory=set)
    auth_cookies: dict[str, str] = field(default_factory=dict)
    auth_headers: dict[str, str] = field(default_factory=dict)
    agent_log: list[str] = field(default_factory=list)
    agent_summary: str = ""        # ← NUEVO: análisis del LLM
    report_path: Optional[str] = None
