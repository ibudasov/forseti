"""Offline tests for the go-live tooling: trust check, demo check, doc coverage and exit criteria."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import cloud_trust_check, demo_check, doc_coverage
from scripts.cloud_scorecard import exit_criteria
from scripts.cloud_scorecard.model import Status
from tests.test_cloud_scorecard import environment

ROOT = Path(__file__).resolve().parents[1]


def analysis(*, downgraded=(), fallbacks=(), unparsable=False, latency=1000.0) -> dict:
    return {
        "total_latency_ms": latency,
        "downgraded_agents": list(downgraded),
        "degraded_flags": {
            "unparsable_synthesis": unparsable,
            "agent_fallbacks": list(fallbacks),
            "degraded_agents": [],
        },
    }


def run(agentic: dict, violations=(), errors=None) -> dict:
    return {
        "agentic": agentic,
        "invariants": {"passed": not violations, "results": list(violations)},
        "errors": errors or {},
    }


def report(runs: list[dict], pending: int = 0) -> dict:
    return {
        "runs": runs,
        "summary": {
            "latency_ms": {"p50": 900.0, "p95": 1500.0},
            "estimated_cost_usd": {"total": 0.04, "pending_run_count": pending},
        },
    }


class TestTrustSummary:
    def test_clean_report_has_no_hard_failures(self):
        summary = cloud_trust_check.summarize(
            report([run(analysis(downgraded=["critic_guardrail"])), run(analysis(fallbacks=["technical"]))])
        )

        assert cloud_trust_check.hard_failures(summary) == []
        assert summary["downgrade_rate_per_agent"] == {"critic_guardrail": 0.5}
        assert summary["fallback_rate"] == 0.5
        assert summary["cost_per_analysis_usd"] == pytest.approx(0.02)

    def test_violations_and_unparsable_synthesis_block_go_live(self):
        summary = cloud_trust_check.summarize(
            report([run(analysis(unparsable=True), violations=[{"name": "risk_numbers"}])])
        )

        assert cloud_trust_check.hard_failures(summary) == [
            "1 invariant violation(s)",
            "1 unparsable_synthesis run(s)",
        ]

    def test_errored_run_blocks_go_live(self):
        errored = {"agentic": None, "invariants": None, "errors": {"agentic": "boom"}}

        failures = cloud_trust_check.hard_failures(cloud_trust_check.summarize(report([errored])))

        assert "1 run(s) errored" in failures

    def test_comparison_shows_cloud_and_baseline_columns(self):
        cloud = cloud_trust_check.summarize(report([run(analysis(downgraded=["technical_analyst"]))]))

        table = cloud_trust_check.render_comparison(cloud, cloud)

        assert "| Invariant violations (must be 0) | 0 | 0 |" in table
        assert "| Downgrade rate: technical_analyst | 100% | 100% |" in table
        assert "n/a" in cloud_trust_check.render_comparison(cloud, None)

    def test_refuses_to_spend_without_cost_confirmation(self, monkeypatch, capsys):
        monkeypatch.delenv("CONFIRM_COST", raising=False)

        assert cloud_trust_check.main([]) == 2
        assert "CONFIRM_COST=yes" in capsys.readouterr().out


class TestDemoCheck:
    @staticmethod
    def api(analysis_status=200, persisted_status=200, trace=True, steps=True):
        def call(method, path, body):
            if method == "POST":
                payload = {"decision": "watchlist", "trace_id": "run-1", "trace": {"steps": []} if trace else None}
                return analysis_status, payload
            assert path == "/runs/run-1"
            return persisted_status, {"steps": [{"agent_name": "x"}] if steps else []}

        return call

    def test_passes_when_each_run_is_persisted_with_a_trace(self):
        assert demo_check.run_demo(self.api(), ("NVDA", "LMT")) == {"NVDA": "watchlist", "LMT": "watchlist"}

    @pytest.mark.parametrize(
        "api",
        [
            {"analysis_status": 502},
            {"trace": False},
            {"persisted_status": 404},
            {"steps": False},
        ],
    )
    def test_fails_when_a_step_breaks(self, api):
        with pytest.raises(demo_check.DemoCheckError):
            demo_check.check_ticker(self.api(**api), "NVDA")


class TestDocCoverage:
    def test_state_addresses_drop_instance_keys_and_data_sources(self):
        names = doc_coverage.resource_names(
            ['google_a.b["x/y"]', "data.google_c.d", "google_a.b", ""]
        )

        assert names == {"google_a.b"}

    def test_reports_resources_missing_from_the_document(self):
        assert doc_coverage.undocumented(["google_a.b", "google_a.c"], "`google_a.b`") == ["google_a.c"]

    def test_architecture_doc_mentions_every_declared_resource(self):
        declared = doc_coverage.declared_resources(ROOT / "infra/terraform/main")
        document = (ROOT / "docs/architecture-gcp.md").read_text()

        assert declared
        assert doc_coverage.undocumented(declared, document) == []


class TestExitCriteria:
    def test_every_exit_check_belongs_to_the_exit_group_and_paid_ones_are_registered(self):
        costs = {check.identifier: check.cost.value for check in exit_criteria.CHECKS}

        assert costs["exit.cloud-trust"] == "paid"
        assert costs["exit.demo"] == "paid"
        assert {check.group for check in exit_criteria.CHECKS} == {"Exit criteria"}

    def test_cloud_trust_is_pending_before_the_job_exists(self):
        env = environment({("run", "jobs", "describe"): None})

        assert exit_criteria.check_cloud_trust(env).status is Status.PENDING

    @pytest.mark.parametrize(
        ("completed", "expected"), [("True", Status.PASS), ("False", Status.FAIL)]
    )
    def test_cloud_trust_follows_the_job_execution(self, completed, expected):
        execution = {"status": {"conditions": [{"type": "Completed", "status": completed}]}}
        env = environment({
            ("run", "jobs", "describe"): {"metadata": {"name": "forseti-trust-check"}},
            ("run", "jobs", "execute"): {"metadata": {"name": "forseti-trust-check-abc"}},
            ("run", "jobs", "executions", "describe"): execution,
        })

        assert exit_criteria.check_cloud_trust(env).status is expected

    @pytest.mark.parametrize(
        ("conclusion", "expected"), [("success", Status.PASS), ("failure", Status.FAIL)]
    )
    def test_doc_coverage_reads_the_latest_nightly_job(self, conclusion, expected):
        runs = "repos/example/repo/actions/workflows/infra.yml/runs?event=schedule&branch=main&status=completed&per_page=1"
        env = environment({}, {
            (runs,): {"workflow_runs": [{"id": 7}]},
            ("repos/example/repo/actions/runs/7/jobs",): {
                "jobs": [{"name": "Documentation coverage", "conclusion": conclusion}]
            },
        })

        assert exit_criteria.check_doc_coverage(env).status is expected

    def test_reproducible_deployment_fails_on_changes_from_other_identities(self):
        runs = "repos/example/repo/actions/workflows/infra.yml/runs?event=schedule&branch=main&status=completed&per_page=1"
        env = environment(
            {("logging", "read"): [{"insertId": "1"}]},
            {(runs,): {"workflow_runs": [{"conclusion": "success"}]}},
        )

        assert exit_criteria.check_reproducible_deployment(env).status is Status.FAIL
