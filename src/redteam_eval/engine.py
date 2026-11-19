"""Evaluation, transparent indicators, aggregation, and export utilities."""

from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass
from io import StringIO
import time

from .catalog import SCENARIOS, Scenario
from .targets import Target, TargetError


PREVIEW_LIMIT = 400


@dataclass(frozen=True)
class Finding:
    scenario_id: str
    title: str
    category: str
    owasp_reference: str
    status: str
    indicator: str
    response_preview: str
    duration_ms: float


def run_scenario(target: Target, scenario: Scenario) -> Finding:
    started = time.perf_counter()
    try:
        response = target.complete(scenario)
        if not isinstance(response, str):
            raise TargetError("Target did not return text")
        marker = scenario.marker.casefold()
        match_at = response.casefold().find(marker)
        status = "indicator_observed" if match_at >= 0 else "no_indicator_observed"
        preview = _response_preview(response, marker, match_at)
    except (TargetError, TimeoutError) as exc:
        status = "error"
        preview = f"{type(exc).__name__}: {exc}"[:PREVIEW_LIMIT]
    return Finding(
        scenario_id=scenario.scenario_id,
        title=scenario.title,
        category=scenario.category,
        owasp_reference=scenario.owasp_reference,
        status=status,
        indicator=scenario.marker,
        response_preview=preview,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
    )


def _response_preview(response: str, folded_marker: str, match_at: int) -> str:
    prefix = response[:PREVIEW_LIMIT]
    if match_at < 0 or folded_marker in prefix.casefold():
        return prefix

    # A character can expand when case-folded, so translate the match offset
    # back to the original response before taking the excerpt.
    folded_length = 0
    for index, character in enumerate(response):
        folded_length += len(character.casefold())
        if folded_length > match_at:
            context = min(120, max(0, PREVIEW_LIMIT - 1 - len(folded_marker)))
            start = max(0, index - context)
            if start == 0:
                return prefix
            return "…" + response[start : start + PREVIEW_LIMIT - 1]
    return prefix


def evaluate(target: Target, scenarios: tuple[Scenario, ...] = SCENARIOS) -> dict:
    findings = [run_scenario(target, scenario) for scenario in scenarios]
    counts = Counter(result.status for result in findings)
    valid = len(findings) - counts["error"]
    buckets: dict[str, Counter] = defaultdict(Counter)
    for item in findings:
        buckets[item.category][item.status] += 1
    return {
        "summary": {
            "total": len(findings),
            "evaluated": valid,
            "indicator_observed": counts["indicator_observed"],
            "no_indicator_observed": counts["no_indicator_observed"],
            "errors": counts["error"],
            "indicator_rate_percent": round(100 * counts["indicator_observed"] / valid, 1)
            if valid else None,
        },
        "categories": [
            {
                "category": name,
                "total": sum(category_counts.values()),
                "indicator_observed": category_counts["indicator_observed"],
                "no_indicator_observed": category_counts["no_indicator_observed"],
                "errors": category_counts["error"],
            }
            for name, category_counts in sorted(buckets.items())
        ],
        "findings": [asdict(item) for item in findings],
        "disclaimer": (
            "Reference results are simulated. Local model results come from benign control "
            "exercises. Exact text matches are indicators for review and may miss other behaviour."
        ),
    }


def report_csv(results: dict) -> str:
    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(Finding.__dataclass_fields__))
    writer.writeheader()
    for finding in results["findings"]:
        writer.writerow({name: _spreadsheet_cell(value) for name, value in finding.items()})
    return stream.getvalue()


def _spreadsheet_cell(value: object) -> object:
    """Treat generated text as data when a spreadsheet opens the report."""
    if isinstance(value, str) and (
        value.startswith(("\t", "\r", "\n"))
        or value.lstrip().startswith(tuple("=+@-＝＋＠－"))
    ):
        return "'" + value
    return value

