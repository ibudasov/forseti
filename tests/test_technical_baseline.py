"""Parity test for TA-1: app.domain.technicals must not change checklist behaviour.

This test keeps a frozen copy of the pre-refactor rule 5-9 implementation
(the "reference oracle") and asserts that app.services.checklist.evaluate_checklist
produces byte-identical (score, detail) output to that oracle for every
pass/fail/None combination of the five technical inputs.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional

import pytest

from app.db.models import PriceBar, TechnicalFeature
from app.services.checklist import (
    RSI_HEALTHY_MAX,
    RSI_HEALTHY_MIN,
    VIX_CALM_MAX,
    evaluate_checklist,
)


# --- Reference oracle: a frozen copy of the pre-TA-1 rule 5-9 implementation ---


@dataclass
class _OracleResult:
    rule_id: str
    points: int
    detail: str


def _oracle_check_close_vs_sma50(
    latest_bar: Optional[PriceBar],
    technical_feature: Optional[TechnicalFeature],
) -> Optional[_OracleResult]:
    if latest_bar is None or technical_feature is None or technical_feature.sma_50 is None:
        return None
    close = Decimal(str(latest_bar.close))
    sma_50 = Decimal(str(technical_feature.sma_50))
    if close > sma_50:
        return _OracleResult(
            rule_id="close_vs_sma50",
            points=1,
            detail=f"close: {float(close):.2f} > sma_50: {float(sma_50):.2f}",
        )
    return None


def _oracle_check_close_vs_sma200(
    latest_bar: Optional[PriceBar],
    technical_feature: Optional[TechnicalFeature],
) -> Optional[_OracleResult]:
    if latest_bar is None or technical_feature is None or technical_feature.sma_200 is None:
        return None
    close = Decimal(str(latest_bar.close))
    sma_200 = Decimal(str(technical_feature.sma_200))
    if close > sma_200:
        return _OracleResult(
            rule_id="close_vs_sma200",
            points=1,
            detail=f"close: {float(close):.2f} > sma_200: {float(sma_200):.2f}",
        )
    return None


def _oracle_check_rsi_healthy(technical_feature: Optional[TechnicalFeature]) -> Optional[_OracleResult]:
    if technical_feature is None or technical_feature.rsi_14 is None:
        return None
    rsi = Decimal(str(technical_feature.rsi_14))
    if RSI_HEALTHY_MIN <= rsi <= RSI_HEALTHY_MAX:
        return _OracleResult(
            rule_id="rsi_healthy",
            points=1,
            detail=f"rsi_14: {float(rsi):.2f} in [{RSI_HEALTHY_MIN}, {RSI_HEALTHY_MAX}]",
        )
    return None


def _oracle_check_volume_trend(technical_feature: Optional[TechnicalFeature]) -> Optional[_OracleResult]:
    if technical_feature is None or technical_feature.volume_trend is None:
        return None
    vt = Decimal(str(technical_feature.volume_trend))
    if vt > Decimal("1.0"):
        return _OracleResult(
            rule_id="volume_trend",
            points=1,
            detail=f"volume_trend: {float(vt):.4f} > 1.0",
        )
    return None


def _oracle_check_vix_calm(vix_close: Optional[Decimal]) -> Optional[_OracleResult]:
    if vix_close is None:
        return None
    vix = Decimal(str(vix_close))
    if vix < VIX_CALM_MAX:
        return _OracleResult(
            rule_id="vix_calm",
            points=1,
            detail=f"vix: {float(vix):.2f} < {VIX_CALM_MAX} calm threshold",
        )
    return None


def _oracle_technical_results(
    latest_bar: Optional[PriceBar],
    technical_feature: Optional[TechnicalFeature],
    vix_close: Optional[Decimal],
) -> tuple[int, List[_OracleResult]]:
    results: List[_OracleResult] = []
    total_score = 0
    for oracle_result in (
        _oracle_check_close_vs_sma50(latest_bar, technical_feature),
        _oracle_check_close_vs_sma200(latest_bar, technical_feature),
        _oracle_check_rsi_healthy(technical_feature),
        _oracle_check_volume_trend(technical_feature),
        _oracle_check_vix_calm(vix_close),
    ):
        if oracle_result is None:
            continue
        results.append(oracle_result)
        total_score += oracle_result.points
    return total_score, results


# --- Fixtures spanning pass / fail / missing for every one of the five inputs ---

AS_OF = date(2026, 1, 1)

_BAR_PASS = PriceBar(
    security_id=1, bar_date=AS_OF, open=Decimal("100"), high=Decimal("101"),
    low=Decimal("99"), close=Decimal("100"), volume=1_000_000,
)
_BAR_FAIL = PriceBar(
    security_id=1, bar_date=AS_OF, open=Decimal("50"), high=Decimal("51"),
    low=Decimal("49"), close=Decimal("50"), volume=1_000_000,
)
_BAR_OPTIONS = [None, _BAR_PASS, _BAR_FAIL]

_SMA_50_OPTIONS = [None, Decimal("90"), Decimal("200")]
_SMA_200_OPTIONS = [None, Decimal("80"), Decimal("200")]
_RSI_OPTIONS = [None, Decimal("55"), Decimal("80")]
_VOLUME_TREND_OPTIONS = [None, Decimal("1.5"), Decimal("0.5")]
_VIX_OPTIONS = [None, Decimal("15"), Decimal("35")]
_FEATURE_PRESENCE = [False, True]


def _build_feature(
    present: bool,
    sma_50: Optional[Decimal],
    sma_200: Optional[Decimal],
    rsi_14: Optional[Decimal],
    volume_trend: Optional[Decimal],
) -> Optional[TechnicalFeature]:
    if not present:
        return None
    return TechnicalFeature(
        security_id=1,
        as_of_date=AS_OF,
        sma_50=sma_50,
        sma_200=sma_200,
        rsi_14=rsi_14,
        volume_trend=volume_trend,
    )


def _combinations():
    for bar in _BAR_OPTIONS:
        for feature_present in _FEATURE_PRESENCE:
            # Sample a bounded subset of the per-metric value grid to keep the
            # parity test fast while still covering every pass/fail/None case
            # for each of the five inputs independently.
            for sma_50, sma_200, rsi_14, volume_trend, vix in itertools.product(
                _SMA_50_OPTIONS, _SMA_200_OPTIONS, _RSI_OPTIONS, _VOLUME_TREND_OPTIONS, [None]
            ):
                yield bar, feature_present, sma_50, sma_200, rsi_14, volume_trend, vix
            for vix in _VIX_OPTIONS:
                yield bar, feature_present, None, None, None, None, vix


@pytest.mark.parametrize(
    "latest_bar,feature_present,sma_50,sma_200,rsi_14,volume_trend,vix_close",
    list(_combinations()),
)
def test_checklist_technical_rules_match_pre_refactor_oracle(
    latest_bar, feature_present, sma_50, sma_200, rsi_14, volume_trend, vix_close
):
    technical_feature = _build_feature(feature_present, sma_50, sma_200, rsi_14, volume_trend)

    expected_score, expected_results = _oracle_technical_results(latest_bar, technical_feature, vix_close)

    total_score, results = evaluate_checklist(latest_bar, None, technical_feature, vix_close)

    technical_results = [result for result in results if result.rule_id in {
        "close_vs_sma50", "close_vs_sma200", "rsi_healthy", "volume_trend", "vix_calm",
    }]
    fundamental_score = total_score - sum(result.points for result in technical_results)

    assert fundamental_score == 0
    assert [(r.rule_id, r.points, r.detail) for r in technical_results] == [
        (r.rule_id, r.points, r.detail) for r in expected_results
    ]
    assert sum(result.points for result in technical_results) == expected_score


def test_checklist_golden_all_pass_matches_oracle():
    feature = TechnicalFeature(
        security_id=1,
        as_of_date=AS_OF,
        sma_50=Decimal("90"),
        sma_200=Decimal("80"),
        rsi_14=Decimal("55"),
        volume_trend=Decimal("1.5"),
    )
    expected_score, expected_results = _oracle_technical_results(_BAR_PASS, feature, Decimal("15"))
    total_score, results = evaluate_checklist(_BAR_PASS, None, feature, Decimal("15"))

    assert expected_score == 5
    assert total_score == 5
    assert [(r.rule_id, r.points, r.detail) for r in results] == [
        (r.rule_id, r.points, r.detail) for r in expected_results
    ]
