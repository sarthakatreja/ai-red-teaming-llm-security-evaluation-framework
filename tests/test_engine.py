"""Behavioral tests for the deterministic evaluation pipeline."""

import csv
from io import StringIO
import unittest
from unittest.mock import Mock

from redteam_eval.catalog import SCENARIOS
from redteam_eval.engine import evaluate, report_csv, run_scenario
from redteam_eval.targets import OllamaTarget, ReferenceTarget, TargetError


class BrokenTarget:
    def complete(self, scenario):
        raise TargetError("simulated disconnected target")


class HarmlessTarget:
    def complete(self, scenario):
        return "I will not repeat hidden test markers."


class EvaluationTests(unittest.TestCase):
    def test_catalog_contains_unique_cases(self):
        self.assertEqual(len(SCENARIOS), 8)
        self.assertEqual(len({s.scenario_id for s in SCENARIOS}), len(SCENARIOS))
        self.assertTrue(all(s.owasp_reference for s in SCENARIOS))

    def test_vulnerable_reference_observes_all_indicators(self):
        result = evaluate(ReferenceTarget("vulnerable"))
        self.assertEqual(result["summary"]["indicator_observed"], 8)
        self.assertEqual(result["summary"]["errors"], 0)
        self.assertEqual(result["summary"]["indicator_rate_percent"], 100.0)

    def test_secure_reference_observes_no_indicators(self):
        result = evaluate(ReferenceTarget("secure"))
        self.assertEqual(result["summary"]["no_indicator_observed"], 8)
        self.assertEqual(result["summary"]["indicator_rate_percent"], 0.0)

    def test_errors_are_not_counted_as_safe(self):
        result = evaluate(BrokenTarget())
        self.assertEqual(result["summary"]["errors"], 8)
        self.assertEqual(result["summary"]["evaluated"], 0)
        self.assertIsNone(result["summary"]["indicator_rate_percent"])

    def test_category_breakdown_and_rate_for_mixed_results(self):
        class MixedTarget:
            def complete(self, scenario):
                if scenario.scenario_id == "PI-001":
                    return scenario.marker.lower()
                if scenario.scenario_id == "PI-002":
                    raise TargetError("simulated request failure")
                return "Public summary."

        result = evaluate(MixedTarget())
        self.assertEqual(result["summary"]["evaluated"], 7)
        self.assertEqual(result["summary"]["indicator_rate_percent"], 14.3)
        prompt_injection = next(
            category for category in result["categories"] if category["category"] == "Prompt injection"
        )
        self.assertEqual(prompt_injection, {
            "category": "Prompt injection",
            "total": 3,
            "indicator_observed": 1,
            "no_indicator_observed": 1,
            "errors": 1,
        })

    def test_document_cases_flag_the_label_on_its_own(self):
        expected_markers = {
            "PI-002": "DEMO_DOCUMENT_LABEL",
            "AU-002": "DEMO_TOOL_EVENT",
            "AC-001": "TENANT_B_DOCUMENT",
        }
        scenarios = {scenario.scenario_id: scenario for scenario in SCENARIOS}
        for scenario_id, marker in expected_markers.items():
            with self.subTest(scenario=scenario_id):
                target = Mock()
                target.complete.return_value = marker.lower()
                finding = run_scenario(target, scenarios[scenario_id])
                self.assertEqual(finding.status, "indicator_observed")
                self.assertEqual(finding.indicator, marker)

    def test_message_and_document_controls_ignore_unrelated_labels(self):
        scenarios = {scenario.scenario_id: scenario for scenario in SCENARIOS}
        cases = (
            ("PI-001", "DEMO_DOCUMENT_LABEL"),
            ("PI-002", "DEMO_OVERRIDE_LABEL"),
        )
        for scenario_id, unrelated_label in cases:
            with self.subTest(scenario=scenario_id):
                target = Mock()
                target.complete.return_value = unrelated_label
                finding = run_scenario(target, scenarios[scenario_id])
                self.assertEqual(finding.status, "no_indicator_observed")

    def test_response_preview_is_bounded(self):
        class LongTarget:
            def complete(self, scenario):
                return "A" * 2000

        finding = run_scenario(LongTarget(), SCENARIOS[0])
        self.assertEqual(len(finding.response_preview), 400)

    def test_late_match_is_visible_in_bounded_original_text(self):
        class LateMarkerTarget:
            def complete(self, scenario):
                return "ß" * 550 + " Safe demo excerpt: " + scenario.marker.lower() + " end."

        scenario = SCENARIOS[0]
        finding = run_scenario(LateMarkerTarget(), scenario)
        self.assertEqual(finding.status, "indicator_observed")
        self.assertLessEqual(len(finding.response_preview), 400)
        self.assertTrue(finding.response_preview.startswith("…"))
        self.assertIn(f"Safe demo excerpt: {scenario.marker.lower()}", finding.response_preview)

    def test_csv_export_contains_one_row_per_finding(self):
        result = evaluate(HarmlessTarget())
        rows = list(csv.DictReader(StringIO(report_csv(result))))
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["scenario_id"], "PI-001")

    def test_csv_export_keeps_formula_like_text_and_newlines_in_one_cell(self):
        class SpreadsheetTarget:
            def complete(self, scenario):
                return '  =1+1\n"Fictional fixture"'

        result = evaluate(SpreadsheetTarget(), SCENARIOS[:1])
        row = next(csv.DictReader(StringIO(report_csv(result))))
        self.assertEqual(row["response_preview"], '\'  =1+1\n"Fictional fixture"')

    def test_ollama_model_name_is_strictly_validated(self):
        with self.assertRaises(ValueError):
            OllamaTarget("../secrets")
        with self.assertRaises(ValueError):
            OllamaTarget("valid:model", timeout_seconds=1000)


if __name__ == "__main__":
    unittest.main()
