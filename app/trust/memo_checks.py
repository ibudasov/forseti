from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from app.schemas.analyze import AnalyzeResponse
from app.schemas.fundamentals import FundamentalAnalysisRequest, FundamentalAssessmentResponse
from app.schemas.technicals import TechnicalAnalysisRequest, TechnicalAssessmentResponse

ABSOLUTE_TOLERANCE = 0.01
_NUMBER = r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?"
_RATIO_NUMBER = r"[-+]?\d+(?:\.\d+)?"
_WINDOW = 48
_Decision = Literal["trade", "watchlist", "no_trade"]
_Severity = Literal["warning", "failure"]


@dataclass(frozen=True)
class MemoCheckIssue:
    code: str
    severity: _Severity
    field_name: str
    message: str
    claim_text: str = ""
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True)
class MemoCheckResult:
    issues: tuple[MemoCheckIssue, ...]

    @property
    def passed(self) -> bool:
        return not any(issue.severity == "failure" for issue in self.issues)

    @property
    def failure_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "failure")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")


@dataclass(frozen=True)
class TechnicalIndicatorSnapshot:
    rsi_14: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    vix: float | None = None
    volume_trend: float | None = None


@dataclass(frozen=True)
class CitationResolutionIssue:
    finding_id: str
    citation_type: Literal["metric_id", "chunk_id"]
    missing_ids: tuple[str, ...]


@dataclass(frozen=True)
class CitationResolutionResult:
    run_id: str
    context_hash: str
    issues: tuple[CitationResolutionIssue, ...]

    @property
    def unknown_id_count(self) -> int:
        return sum(len(issue.missing_ids) for issue in self.issues)


def check_memo_consistency(
    memo_text: str,
    response: AnalyzeResponse,
    indicators: TechnicalIndicatorSnapshot | None = None,
) -> MemoCheckResult:
    issues = [
        *check_memo_risk_numbers(memo_text, response),
        *check_memo_decision_labels(memo_text, response.decision),
        *check_memo_indicator_values(memo_text, indicators),
    ]
    return MemoCheckResult(issues=tuple(issues))


def check_memo_risk_numbers(memo_text: str, response: AnalyzeResponse) -> list[MemoCheckIssue]:
    issues: list[MemoCheckIssue] = []
    checks = (
        _single_number_check("stop_loss", response.stop_loss, _STOP_LOSS_PATTERNS),
        _single_number_check("position_size_eur", response.position_size_eur, _POSITION_SIZE_PATTERNS),
        _single_number_check("risk_reward", response.risk_reward, _RISK_REWARD_PATTERNS),
        _range_check("entry_range", response.entry_range, _ENTRY_RANGE_PATTERNS),
        _range_check("take_profit", response.take_profit, _TAKE_PROFIT_PATTERNS),
    )
    for check in checks:
        issues.extend(check(memo_text))
    return issues


def check_memo_decision_labels(memo_text: str, expected_decision: _Decision) -> list[MemoCheckIssue]:
    seen_labels = _extract_decision_labels(memo_text)
    return [
        MemoCheckIssue(
            code="decision_label_mismatch",
            severity="failure",
            field_name="decision",
            message=f"Memo decision label '{label}' does not match final decision '{expected_decision}'.",
            claim_text=matched_text,
            expected=expected_decision,
            actual=label,
        )
        for label, matched_text in seen_labels
        if label != expected_decision
    ]


def check_memo_indicator_values(
    memo_text: str,
    indicators: TechnicalIndicatorSnapshot | None,
) -> list[MemoCheckIssue]:
    if indicators is None:
        return []
    issues: list[MemoCheckIssue] = []
    indicator_checks = (
        ("rsi_14", indicators.rsi_14, _RSI_PATTERNS),
        ("sma_50", indicators.sma_50, _SMA50_PATTERNS),
        ("sma_200", indicators.sma_200, _SMA200_PATTERNS),
        ("vix", indicators.vix, _VIX_PATTERNS),
        ("volume_trend", indicators.volume_trend, _VOLUME_TREND_PATTERNS),
    )
    for field_name, expected_value, patterns in indicator_checks:
        issues.extend(_single_number_check(field_name, expected_value, patterns)(memo_text))
    return issues


def resolve_fundamental_citations(
    response: FundamentalAssessmentResponse,
    request: FundamentalAnalysisRequest,
) -> CitationResolutionResult:
    known_metric_ids = {
        point.metric_id
        for series in request.metric_series
        for point in [*series.annual, *series.quarterly]
    }
    known_metric_ids.update(metric.metric_id for metric in request.deterministic_result.metrics)
    known_chunk_ids = {str(chunk.chunk_id) for chunk in request.evidence_chunks}

    return CitationResolutionResult(
        run_id=response.run_id,
        context_hash=response.context_hash,
        issues=tuple(
            _citation_resolution_issues(
                _all_findings(response),
                known_metric_ids=known_metric_ids,
                known_chunk_ids=known_chunk_ids,
            )
        ),
    )


def resolve_technical_citations(
    response: TechnicalAssessmentResponse,
    request: TechnicalAnalysisRequest,
) -> CitationResolutionResult:
    """Grounding check mirroring `resolve_fundamental_citations` for the
    Technical Analyst: every cited `metric_id` must resolve to a known
    deterministic fact or metric. Technical findings never carry `chunk_ids`.
    """
    known_metric_ids = {fact.metric_id for fact in request.facts}
    known_metric_ids.update(metric.metric_id for metric in request.deterministic_result.metrics)

    return CitationResolutionResult(
        run_id=response.run_id,
        context_hash=response.context_hash,
        issues=tuple(
            _citation_resolution_issues(
                _all_technical_findings(response),
                known_metric_ids=known_metric_ids,
                known_chunk_ids=set(),
            )
        ),
    )


def _citation_resolution_issues(
    findings: Iterable,
    *,
    known_metric_ids: set[str],
    known_chunk_ids: set[str],
) -> list[CitationResolutionIssue]:
    issues: list[CitationResolutionIssue] = []
    for finding in findings:
        missing_metric_ids = tuple(sorted(set(finding.metric_ids) - known_metric_ids))
        if missing_metric_ids:
            issues.append(
                CitationResolutionIssue(
                    finding_id=finding.finding_id,
                    citation_type="metric_id",
                    missing_ids=missing_metric_ids,
                )
            )
        chunk_ids = getattr(finding, "chunk_ids", [])
        missing_chunk_ids = tuple(sorted({str(chunk_id) for chunk_id in chunk_ids} - known_chunk_ids))
        if missing_chunk_ids:
            issues.append(
                CitationResolutionIssue(
                    finding_id=finding.finding_id,
                    citation_type="chunk_id",
                    missing_ids=missing_chunk_ids,
                )
            )
    return issues


def _all_findings(response: FundamentalAssessmentResponse):
    return [*response.findings, *response.contradictions, *response.material_red_flags]


def _all_technical_findings(response: TechnicalAssessmentResponse):
    return [*response.findings, *response.contradictions, *response.risk_flags]


def _single_number_check(
    field_name: str,
    expected_value: float | None,
    patterns: Sequence[re.Pattern[str]],
):
    def _checker(memo_text: str) -> list[MemoCheckIssue]:
        issues: list[MemoCheckIssue] = []
        for matched_text, actual_value in _extract_first_number_matches(memo_text, patterns):
            if expected_value is None:
                issues.append(
                    MemoCheckIssue(
                        code="unmatched_numeric_claim",
                        severity="warning",
                        field_name=field_name,
                        message=f"Memo mentions '{field_name}' but no deterministic value is available.",
                        claim_text=matched_text,
                        actual=_format_number(actual_value),
                    )
                )
                continue
            if _numbers_match(actual_value, expected_value):
                continue
            issues.append(
                MemoCheckIssue(
                    code="numeric_claim_mismatch",
                    severity="failure",
                    field_name=field_name,
                    message=f"Memo {field_name} value does not match the deterministic output.",
                    claim_text=matched_text,
                    expected=_format_number(expected_value),
                    actual=_format_number(actual_value),
                )
            )
        return issues

    return _checker


def _range_check(
    field_name: str,
    expected_range: tuple[float, float] | None,
    patterns: Sequence[re.Pattern[str]],
):
    def _checker(memo_text: str) -> list[MemoCheckIssue]:
        issues: list[MemoCheckIssue] = []
        for matched_text, actual_range in _extract_first_range_matches(memo_text, patterns):
            if expected_range is None:
                issues.append(
                    MemoCheckIssue(
                        code="unmatched_numeric_claim",
                        severity="warning",
                        field_name=field_name,
                        message=f"Memo mentions '{field_name}' but no deterministic range is available.",
                        claim_text=matched_text,
                        actual=_format_range(actual_range),
                    )
                )
                continue
            if _ranges_match(actual_range, expected_range):
                continue
            issues.append(
                MemoCheckIssue(
                    code="numeric_claim_mismatch",
                    severity="failure",
                    field_name=field_name,
                    message=f"Memo {field_name} range does not match the deterministic output.",
                    claim_text=matched_text,
                    expected=_format_range(expected_range),
                    actual=_format_range(actual_range),
                )
            )
        return issues

    return _checker


def _extract_first_range_matches(
    memo_text: str,
    patterns: Sequence[re.Pattern[str]],
) -> Iterable[tuple[str, tuple[float, float]]]:
    seen_spans: set[tuple[int, int]] = set()
    for pattern in patterns:
        for match in pattern.finditer(memo_text):
            if match.span() in seen_spans:
                continue
            first = _parse_number(match.group("first"))
            second = _parse_number(match.group("second"))
            if first is None or second is None:
                continue
            seen_spans.add(match.span())
            yield match.group(0), (first, second)


def _extract_decision_labels(memo_text: str) -> list[tuple[_Decision, str]]:
    matches: list[tuple[int, int, _Decision, str]] = []
    for pattern, label in _DECISION_PATTERNS:
        for match in pattern.finditer(memo_text):
            span = match.span()
            if any(_overlaps(span, (start, end)) for start, end, _, _ in matches):
                continue
            matches.append((span[0], span[1], label, match.group(0)))
    matches.sort(key=lambda item: item[0])
    return [(label, text) for _, _, label, text in matches]


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _parse_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    normalized = raw.replace(",", "").strip()
    if not normalized:
        return None
    return float(normalized)


def _numbers_match(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=ABSOLUTE_TOLERANCE)


def _ranges_match(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return _numbers_match(left[0], right[0]) and _numbers_match(left[1], right[1])


def _format_number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _format_range(value: tuple[float, float]) -> str:
    return f"{_format_number(value[0])} - {_format_number(value[1])}"


def _keyword_number_patterns(*keywords: str) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for keyword in keywords:
        compiled.append(
            re.compile(
                rf"\b{keyword}\b[^.\n]{{0,{_WINDOW}}}?(?P<value>{_NUMBER})",
                re.IGNORECASE,
            )
        )
    return tuple(compiled)


def _keyword_range_patterns(*keywords: str) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for keyword in keywords:
        compiled.append(
            re.compile(
                rf"\b{keyword}\b[^.\n]{{0,{_WINDOW}}}?(?P<first>{_NUMBER})\s*(?:-|to|and)\s*(?P<second>{_NUMBER})",
                re.IGNORECASE,
            )
        )
    return tuple(compiled)


def _risk_reward_patterns() -> tuple[re.Pattern[str], ...]:
    return (
        re.compile(
            rf"\b(?:r/?r|risk\s*/?\s*reward|risk[- ]reward)\b[^.\n]{{0,{_WINDOW}}}?(?P<value>{_NUMBER})\s*(?:x|to\s*1)?",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:r/?r|risk\s*/?\s*reward|risk[- ]reward)\b"
            rf"[^.\n]{{0,{_WINDOW}}}?(?P<left>{_RATIO_NUMBER})\s*:\s*(?P<right>{_RATIO_NUMBER})",
            re.IGNORECASE,
        ),
    )


def _extract_first_number_matches_for_ratio(
    memo_text: str,
    patterns: Sequence[re.Pattern[str]],
) -> Iterable[tuple[str, float]]:
    seen_spans: set[tuple[int, int]] = set()
    for pattern in patterns:
        for match in pattern.finditer(memo_text):
            if match.span() in seen_spans:
                continue
            if "left" in match.groupdict() and "right" in match.groupdict():
                left = _parse_number(match.group("left"))
                right = _parse_number(match.group("right"))
                if left is None or right in {None, 0.0}:
                    continue
                seen_spans.add(match.span())
                yield match.group(0), left / right
                continue
            value = _parse_number(match.group("value"))
            if value is None:
                continue
            seen_spans.add(match.span())
            yield match.group(0), value


_ENTRY_RANGE_PATTERNS = _keyword_range_patterns("entry", "entry range")
_TAKE_PROFIT_PATTERNS = _keyword_range_patterns("take profit", "target", "targets")
_STOP_LOSS_PATTERNS = _keyword_number_patterns("stop", "stop loss", "stop-loss")
_POSITION_SIZE_PATTERNS = _keyword_number_patterns("position size", "size")
_RISK_REWARD_PATTERNS = _risk_reward_patterns()
_RSI_PATTERNS = _keyword_number_patterns("rsi", "rsi 14", "rsi-14")
_SMA50_PATTERNS = _keyword_number_patterns("sma50", "sma 50", "50 day sma", "50-day sma")
_SMA200_PATTERNS = _keyword_number_patterns("sma200", "sma 200", "200 day sma", "200-day sma")
_VIX_PATTERNS = _keyword_number_patterns("vix")
_VOLUME_TREND_PATTERNS = _keyword_number_patterns("volume trend", "volume_trend")
_DECISION_PATTERNS: tuple[tuple[re.Pattern[str], _Decision], ...] = (
    (re.compile(r"\bnot\s+(?:a\s+)?trade\b", re.IGNORECASE), "no_trade"),
    (re.compile(r"\bno[\s_-]?trade\b", re.IGNORECASE), "no_trade"),
    (re.compile(r"\bpass\b", re.IGNORECASE), "no_trade"),
    (re.compile(r"\bwatch[\s-]?list\b", re.IGNORECASE), "watchlist"),
    (re.compile(r"\btrade\b", re.IGNORECASE), "trade"),
)


def _extract_first_number_matches(
    memo_text: str,
    patterns: Sequence[re.Pattern[str]],
) -> Iterable[tuple[str, float]]:
    if patterns is _RISK_REWARD_PATTERNS:
        yield from _extract_first_number_matches_for_ratio(memo_text, patterns)
        return
    seen_spans: set[tuple[int, int]] = set()
    for pattern in patterns:
        for match in pattern.finditer(memo_text):
            if match.span() in seen_spans:
                continue
            value = _parse_number(match.group("value"))
            if value is None:
                continue
            seen_spans.add(match.span())
            yield match.group(0), value
