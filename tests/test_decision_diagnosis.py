from types import SimpleNamespace

from app.services.decision_diagnosis import (
    from_checklist,
    from_data_gate,
    from_risk_downgrade,
    from_unknown_security,
    from_veto,
)


def test_unknown_security_diagnosis():
    diagnosis = from_unknown_security("NVDA")
    assert diagnosis.stage == "unknown_security"
    assert diagnosis.rule_id == "ticker_not_found"
    assert diagnosis.debug_reason == "unknown_security/ticker_not_found: security not found: NVDA"


def test_data_gate_diagnosis_tracks_only_missing_data_warnings():
    diagnosis = from_data_gate(
        ["insufficient_price_data: fewer than 200 price bars available"],
        ["insufficient_price_data", "no_fundamentals", "unrelated_warning"],
    )
    assert diagnosis.stage == "data_gate"
    assert diagnosis.rule_id == "insufficient_price_data"
    assert diagnosis.missing_data == ["fundamentals", "price_data"]
    assert diagnosis.debug_reason == (
        "data_gate/insufficient_price_data: fewer than 200 price bars available"
    )


def test_checklist_diagnosis_reports_score_band_and_missing_inputs():
    diagnosis = from_checklist(4, [], ["no_technical_features"])
    assert diagnosis.stage == "checklist"
    assert diagnosis.rule_id == "score_below_watchlist"
    assert diagnosis.debug_reason == (
        "checklist/score_below_watchlist: score 4/11, missing: technical_features"
    )


def test_checklist_score_boundaries():
    assert from_checklist(4, [], []).rule_id == "score_below_watchlist"
    assert from_checklist(5, [], []).rule_id == "score_below_trade"
    assert from_checklist(7, [], []).rule_id == "score_below_trade"
    assert from_checklist(8, [], []).rule_id == "checklist_passed"


def test_veto_and_risk_downgrade_diagnoses():
    veto = from_veto(SimpleNamespace(rule_id="earnings_too_close", detail="report on 2026-08-27"))
    downgrade = from_risk_downgrade(
        SimpleNamespace(reason="risk_reward_below_min", detail="risk reward below minimum")
    )
    assert veto.debug_reason == "hard_veto/earnings_too_close: report on 2026-08-27"
    assert downgrade.debug_reason == "risk_math/risk_reward_below_min: risk reward below minimum"


def test_debug_reason_is_single_line_and_capped():
    diagnosis = from_unknown_security("A\n" + "x" * 500)
    assert "\n" not in diagnosis.debug_reason
    assert len(diagnosis.debug_reason) <= 200
