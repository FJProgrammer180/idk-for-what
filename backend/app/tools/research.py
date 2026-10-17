"""Agent tools: research retrieval, report generation, clarification requests."""

from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import Field
from sqlalchemy import select

from app.core.config import settings
from app.core.security import safe_filename, sha256_bytes, utcnow_iso
from app.db.session import session_scope
from app.models.entities import AgentEvent, Experiment, ExperimentRun, Report, ResearchSource
from app.numerical.common import NumericalInputError
from app.services.reporting import build_report, render_pdf, render_markdown_file
from app.tools.base import ToolContext, ToolError, ToolInput, register

# Curated, well-known references shipped with the app.  These are local knowledge
# entries, NOT live retrievals: provenance is stamped accordingly and no URL is
# invented for them.
CURATED_REFERENCES: list[dict[str, str]] = [
    {
        "key": "numerical_recipes",
        "title": "Numerical Recipes: The Art of Scientific Computing (3rd ed.)",
        "authors": "W. H. Press, S. A. Teukolsky, W. T. Vetterling, B. P. Flannery",
        "publisher": "Cambridge University Press",
        "year": "2007",
        "source_type": "reference_text",
        "topics": "root finding, linear systems, interpolation, integration, ODE solvers, "
                  "Richardson extrapolation and Romberg integration (section 5.7)",
        "keywords": "bisection,newton,secant,lu,gaussian,ode,runge,kutta,integration,root,"
                    "richardson,extrapolation,romberg,error estimate,tolerance",
    },
    {
        "key": "richardson_1922",
        "title": "Weather Prediction by Numerical Process",
        "authors": "L. F. Richardson",
        "publisher": "Cambridge University Press",
        "year": "1922",
        "source_type": "primary_classic",
        "topics": "the extrapolation-to-the-limit procedure now used to estimate discretisation "
                  "error by comparing two grid resolutions",
        "keywords": "richardson,extrapolation,error estimate,convergence,grid refinement,limit",
    },
    {
        "key": "hairer_norsett_wanner",
        "title": "Solving Ordinary Differential Equations I: Nonstiff Problems (2nd ed.)",
        "authors": "E. Hairer, S. P. Nørsett, G. Wanner",
        "publisher": "Springer Series in Computational Mathematics",
        "year": "1993",
        "source_type": "reference_text",
        "topics": "Runge-Kutta methods, embedded error estimators, step-size control",
        "keywords": "ode,runge,kutta,rk45,dopri853,explicit,embedded,step control,solver",
    },
    {
        "key": "trefethen_spectral",
        "title": "Spectral Methods in MATLAB",
        "authors": "L. N. Trefethen",
        "publisher": "Society for Industrial and Applied Mathematics (SIAM)",
        "year": "2000",
        "source_type": "reference_text",
        "topics": "spectral discretisation of PDEs, stability of finite-difference schemes",
        "keywords": "pde,heat,diffusion,spectral,stability,finite difference,von neumann",
    },
    {
        "key": "driscoll_fornberg",
        "title": "A brief review of numerical differentiation of data",
        "authors": "T. A. Driscoll, B. Fornberg",
        "publisher": "in: Pickering, Abrahams & Votava (eds.), Recent Developments in Anomalous Propagation",
        "year": "2002",
        "source_type": "secondary_review",
        "topics": "finite-difference weights, noise amplification, choice of step size",
        "keywords": "differentiation,finite difference,noise,step size,derivative",
    },
    {
        "key": "menke_books",
        "title": "Geophysical Data Analysis: Discrete Inverse Theory (3rd ed.)",
        "authors": "W. Menke",
        "publisher": "Academic Press",
        "year": "2012",
        "source_type": "reference_text",
        "topics": "least squares, covariance of estimated parameters, resolution analysis",
        "keywords": "least squares,regression,uncertainty,covariance,inverse problem,residual",
    },
    {
        "key": "bathe_thermo",
        "title": "Thermodynamics: An Engineering Approach (9th ed.)",
        "authors": "Y. A. Cengel, M. A. Boles",
        "publisher": "McGraw-Hill Education",
        "year": "2019",
        "source_type": "reference_text",
        "topics": "lumped-system heat transfer, Biot number validity criterion, Newton's law of cooling",
        "keywords": "cooling,heat,thermodynamics,lumped,biot,newton cooling,convection",
    },
    {
        "key": "goldstein_classical",
        "title": "Classical Mechanics (3rd ed.)",
        "authors": "H. Goldstein, C. Poole, J. Safko",
        "publisher": "Addison-Wesley",
        "year": "2002",
        "source_type": "reference_text",
        "topics": "projectile motion with drag, damped oscillations, pendulum dynamics",
        "keywords": "projectile,drag,pendulum,oscillation,harmonic,mechanics,damping",
    },
    {
        "key": "strogatz_nonlinear",
        "title": "Nonlinear Dynamics and Chaos (2nd ed.)",
        "authors": "S. H. Strogatz",
        "publisher": "Westview Press",
        "year": "2014",
        "source_type": "reference_text",
        "topics": "phase portraits, Lorenz system, sensitivity to initial conditions",
        "keywords": "chaos,lorenz,dynamical system,phase space,attractor,nonlinear",
    },
    {
        "key": "taylor_modern",
        "title": "Classical Mechanics",
        "authors": "J. R. Taylor",
        "publisher": "University Science Books",
        "year": "2005",
        "source_type": "textbook",
        "topics": "terminal velocity, quadratic drag, numerical integration of Newton's law",
        "keywords": "drag,terminal velocity,projectile,numerical integration,newton",
    },
    {
        "key": "scipy_docs",
        "title": "SciPy Reference Guide: integrate.solve_ivp, linalg, optimize.least_squares",
        "authors": "SciPy developers",
        "publisher": "documentation (project website)",
        "year": "current",
        "source_type": "software_documentation",
        "topics": "solver contracts, tolerances, method selection for IVPs",
        "keywords": "scipy,solver,ode,integration,documentation,least squares,radau,bdf",
    },
]


class SearchScientificSourcesInput(ToolInput):
    query: str = Field(min_length=3, max_length=400, description="Research question or keywords.")
    max_results: int = Field(default=5, ge=1, le=10)
    include_local_references: bool = Field(
        default=True,
        description="Also match the bundled, curated reference list (provenance: local, not live).",
    )


@register(
    name="search_scientific_sources",
    category="research",
    read_only=True,
    description=(
        "Retrieve external scientific sources through the configured retrieval integration. "
        "If no integration is configured it reports that external research was NOT performed "
        "and returns only clearly-labelled local curated references. It never invents citations."
    ),
    input_model=SearchScientificSourcesInput,
)
def search_scientific_sources(args: SearchScientificSourcesInput, context: ToolContext) -> dict[str, Any]:
    return _search(args, context)


def _search(args: SearchScientificSourcesInput, context: ToolContext) -> dict[str, Any]:
    local_matches = _local_matches(args.query, args.max_results) if args.include_local_references else []
    if not (settings.research_search_base_url and settings.research_search_api_key):
        return {
            "status": "not_performed",
            "ok": True,
            "external_research_performed": False,
            "reason": (
                "No research retrieval integration is configured on this server "
                "(PLAB_RESEARCH_SEARCH_BASE_URL / PLAB_RESEARCH_SEARCH_API_KEY are unset), "
                "so no external sources were retrieved. Do not present this as a literature review."
            ),
            "local_references": local_matches,
            "local_reference_provenance": "curated_bibliography_bundled_with_the_application",
            "guidance": (
                "Local entries are textbook/documentation citations held in the repository; "
                "they were not verified against a live service in this run."
                if local_matches
                else "Nothing in the bundled bibliography matched these terms and no live "
                     "retrieval is configured, so this response cites nothing. Do not fill the "
                     "gap from memory: consult a library catalogue or a search integration."
            ),
            "query": args.query,
        }
    try:
        response = httpx.get(
            settings.research_search_base_url,
            params={"q": args.query, "limit": args.max_results},
            headers={"Authorization": f"Bearer {settings.research_search_api_key}"},
            timeout=settings.research_timeout_seconds,
            follow_redirects=False,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "ok": False,
            "external_research_performed": False,
            "reason": f"configured retrieval endpoint could not be used: {type(exc).__name__}",
            "local_references": local_matches,
        }
    items = data.get("results") or data.get("items") or []
    sources = []
    for item in items[: args.max_results]:
        url = str(item.get("url") or item.get("link") or "")
        if not url.startswith(("https://", "http://")):
            continue
        sources.append(
            {
                "title": str(item.get("title") or "(untitled)")[:400],
                "url": url[:1000],
                "publisher": str(item.get("publisher") or item.get("site_name") or "")[:200] or None,
                "retrieved_at": utcnow_iso(),
                "evidence": str(item.get("snippet") or item.get("abstract") or "")[:1200],
                "source_type": "external_search_result",
                "provenance": "live_retrieval",
            }
        )
    persisted = _persist_sources(context, sources)
    return {
        "status": "completed",
        "external_research_performed": True,
        "query": args.query,
        "sources": sources,
        "persisted_source_ids": persisted,
        "local_references": local_matches,
        "caveat": "abstracts are retrieval snippets, not full-text verification of claims",
    }


def _local_matches(query: str, limit: int) -> list[dict[str, Any]]:
    tokens = {t for t in "".join(c.lower() if c.isalnum() else " " for c in query).split() if len(t) > 3}
    scored: list[tuple[int, dict[str, Any]]] = []
    for ref in CURATED_REFERENCES:
        haystack = " ".join(
            [ref["title"].lower(), ref["topics"].lower(), ref["keywords"].lower(), ref["publisher"].lower()]
        )
        score = sum(1 for token in tokens if token in haystack)
        if score:
            scored.append(
                (
                    score,
                    {
                        "title": ref["title"],
                        "authors": ref["authors"],
                        "publisher": ref["publisher"],
                        "year": ref["year"],
                        "source_type": ref["source_type"],
                        "provenance": "local_curated_reference",
                        "relevance": f"matched {score} query term(s); topics: {ref['topics']}",
                        "url": None,
                        "verified_online": False,
                    },
                )
            )
    scored.sort(key=lambda pair: -pair[0])
    return [entry for _score, entry in scored[:limit]]


def _persist_sources(context: ToolContext, sources: list[dict[str, Any]]) -> list[str]:
    if not sources:
        return []
    ids: list[str] = []
    with session_scope() as db:
        for source in sources:
            row = ResearchSource(
                owner_id=context.user_id,
                project_id=context.project_id,
                session_id=context.session_id,
                title=source["title"][:400],
                url=source.get("url"),
                publisher=source.get("publisher"),
                source_type=source.get("source_type", "external_search_result"),
                provenance=source.get("provenance", "live_retrieval"),
                citation=f"{source['title']} — {source.get('publisher') or 'unknown publisher'}",
                evidence=(source.get("evidence") or "")[:4000],
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
    return ids


class GenerateResearchReportInput(ToolInput):
    experiment_id: str | None = Field(default=None, description="Experiment to report on.")
    run_id: str | None = Field(default=None, description="Specific run (defaults to the latest).")
    include_verification: bool = Field(default=True)
    include_iterations: bool = Field(default=True)
    source_ids: list[str] = Field(default_factory=list, max_length=20,
                                  description="Persisted ResearchSource ids to cite.")
    format: Literal["markdown", "pdf", "both"] = Field(default="both")
    notes: str | None = Field(default=None, max_length=2000, description="Discussion notes to include.")


@register(
    name="generate_research_report",
    category="reporting",
    description=(
        "Compose a structured scientific report strictly from persisted experiment and run "
        "records (equations, inputs, results, iteration tables, verification, software "
        "versions) and export Markdown and/or PDF artifacts."
    ),
    input_model=GenerateResearchReportInput,
)
def generate_research_report(args: GenerateResearchReportInput, context: ToolContext) -> dict[str, Any]:
    with session_scope() as db:
        if args.run_id:
            run = db.get(ExperimentRun, args.run_id)
            if run is None or run.owner_id != context.user_id:
                raise ToolError("forbidden", "run not found in your account")
            experiment = db.get(Experiment, run.experiment_id)
        elif args.experiment_id:
            experiment = db.get(Experiment, args.experiment_id)
            if experiment is None or experiment.owner_id != context.user_id:
                raise ToolError("forbidden", "experiment not found in your account")
            run = db.execute(
                select(ExperimentRun)
                .where(ExperimentRun.experiment_id == experiment.id)
                .order_by(ExperimentRun.created_at.desc())
            ).scalars().first()
        else:
            raise ToolError("missing_input", "provide experiment_id or run_id")
        if run is None:
            raise ToolError(
                "no_runs",
                "this experiment has no completed runs to report on; run it first "
                "(solve_numerical_problem with persist=true, or POST /experiments/{id}/runs)",
            )
        sources = []
        if args.source_ids:
            rows = db.execute(
                select(ResearchSource).where(
                    ResearchSource.id.in_(args.source_ids), ResearchSource.owner_id == context.user_id
                )
            ).scalars().all()
            sources = [
                {
                    "title": r.title,
                    "url": r.url,
                    "publisher": r.publisher,
                    "citation": r.citation,
                    "provenance": r.provenance,
                    "source_type": r.source_type,
                    "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None,
                }
                for r in rows
            ]
        doc = build_report(
            experiment=experiment,
            run=run,
            sources=sources,
            include_verification=args.include_verification,
            include_iterations=args.include_iterations,
            notes=args.notes,
        )
        settings.ensure_dirs()
        artifacts: dict[str, Any] = {}
        title = doc["title"]
        filename_stem = safe_filename(f"{run.id}_{title}", fallback="report", max_len=90)
        if args.format in ("markdown", "both"):
            md_path = settings.artifacts_dir / f"{filename_stem}.md"
            md_path.write_bytes(render_markdown_file(doc))
            artifacts["markdown"] = {
                "path": str(md_path.relative_to(settings.data_dir)),
                "bytes": md_path.stat().st_size,
                "sha256": sha256_bytes(md_path.read_bytes()),
                "download_url": f"{settings.api_v1_prefix}/reports/{{report_id}}/download?format=markdown",
            }
        if args.format in ("pdf", "both"):
            try:
                pdf_bytes = render_pdf(doc)
            except Exception as exc:  # noqa: BLE001
                artifacts["pdf"] = {"error": f"PDF rendering failed ({type(exc).__name__})"}
            else:
                pdf_path = settings.artifacts_dir / f"{filename_stem}.pdf"
                pdf_path.write_bytes(pdf_bytes)
                artifacts["pdf"] = {
                    "path": str(pdf_path.relative_to(settings.data_dir)),
                    "bytes": len(pdf_bytes),
                    "sha256": sha256_bytes(pdf_bytes),
                    "pages": doc.get("page_count", 1),
                }
        markdown_text = render_markdown_file(doc)
        report = Report(
            owner_id=context.user_id,
            experiment_id=experiment.id if experiment else None,
            run_id=run.id,
            title=title[:200],
            format="markdown+pdf" if args.format == "both" else args.format,
            body_markdown=markdown_text.decode("utf-8"),
            artifact_path=artifacts.get("markdown", {}).get("path"),
            artifact_size_bytes=len(markdown_text),
            checksum_sha256=sha256_bytes(markdown_text),
            verification_snapshot={
                "run_verification_status": (run.verification or {}).get("status"),
                "run_status": run.status,
                "software_versions": run.software_versions,
                "generated_at": utcnow_iso(),
            },
        )
        db.add(report)
        db.flush()
        report_id = report.id
    for key, value in artifacts.items():
        if isinstance(value, dict) and "download_url" in value:
            value["download_url"] = value["download_url"].format(report_id=report_id)
    return {
        "status": "completed",
        "report_id": report_id,
        "title": title,
        "sections": [section["heading"] for section in doc["sections"]],
        "word_count": doc["word_count"],
        "artifacts": artifacts,
        "data_provenance": {
            "experiment_id": doc["experiment_id"],
            "run_id": doc["run_id"],
            "run_status": doc["run_status"],
            "verification_status": doc["verification_status"],
            "cited_sources": len(sources),
            "fabricated_content": False,
        },
        "warnings": doc["warnings"],
        "markdown_preview": markdown_text.decode("utf-8")[:1500],
    }


class RequestClarificationInput(ToolInput):
    question: str = Field(min_length=5, max_length=600, description="Single, specific question for the user.")
    missing_fields: list[str] = Field(default_factory=list, max_length=12,
                                      description="Inputs that are required to continue.")
    why_needed: str | None = Field(default=None, max_length=800,
                                   description="Scientific reason the values cannot be guessed.")
    suggested_defaults: dict[str, Any] = Field(
        default_factory=dict, description="Sensible defaults the user may accept."
    )


@register(
    name="request_clarification",
    category="interaction",
    read_only=True,
    description=(
        "Stop and ask the user for information that is scientifically required (missing "
        "bracket, unknown medium, undefined damping, ambiguous units). Use this instead of "
        "assuming values that change the physics."
    ),
    input_model=RequestClarificationInput,
)
def request_clarification(args: RequestClarificationInput, context: ToolContext) -> dict[str, Any]:
    payload = {
        "status": "awaiting_user_input",
        "ok": True,
        "question": args.question,
        "missing_fields": args.missing_fields,
        "why_needed": args.why_needed,
        "suggested_defaults": args.suggested_defaults,
        "instruction": (
            "The orchestrator will halt this plan; the calculation must not be performed "
            "with invented values."
        ),
    }
    if context.session_id:
        with session_scope() as db:
            sequence = _next_sequence(db, context.session_id)
            db.add(
                AgentEvent(
                    session_id=context.session_id,
                    sequence=sequence,
                    event_type="clarification_requested",
                    status="requires_action",
                    payload=json.loads(json.dumps(payload, default=str)),
                )
            )
    return payload


def _next_sequence(db, session_id: str) -> int:
    last = db.execute(
        select(AgentEvent.sequence).where(AgentEvent.session_id == session_id).order_by(
            AgentEvent.sequence.desc()
        )
    ).scalars().first()
    return int(last) + 1 if last is not None else 1
