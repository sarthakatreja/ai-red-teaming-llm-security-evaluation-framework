"""API integration tests with an in-process ASGI transport."""

from datetime import datetime
import json
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from redteam_eval import __version__
from redteam_eval.targets import TargetError
from redteam_eval.web import app


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        self.addAsyncCleanup(self.client.aclose)

    async def test_health_and_security_headers(self):
        response = await self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["version"], __version__)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_scenario_listing_includes_review_criteria(self):
        response = await self.client.get("/api/scenarios")
        self.assertEqual(response.status_code, 200)
        listing = response.json()["scenarios"]
        self.assertEqual(len(listing), 8)
        self.assertTrue(all(scenario["expected_behaviour"].strip() for scenario in listing))
        self.assertTrue(all("marker" not in scenario for scenario in listing))

    async def test_reference_vulnerable_api(self):
        response = await self.client.post("/api/evaluate", json={"target": "reference_vulnerable"})
        self.assertEqual(response.status_code, 200)
        report = response.json()
        self.assertEqual(report["results"]["summary"]["indicator_observed"], 8)
        self.assertEqual(report["execution"]["mode"], "simulation")
        self.assertIsNone(report["execution"]["model"])
        self.assertIsNotNone(datetime.fromisoformat(report["execution"]["started_at"]).tzinfo)
        self.assertGreaterEqual(report["execution"]["duration_ms"], 0)

    async def test_reference_secure_api(self):
        response = await self.client.post("/api/evaluate", json={"target": "reference_secure"})
        self.assertEqual(response.status_code, 200)
        summary = response.json()["results"]["summary"]
        self.assertEqual(summary["no_indicator_observed"], 8)
        self.assertEqual(summary["indicator_observed"], 0)
        self.assertEqual(summary["errors"], 0)

    async def test_invalid_target_rejected(self):
        response = await self.client.post("/api/evaluate", json={"target": "external_server"})
        self.assertEqual(response.status_code, 422)

    async def test_external_target_parameter_rejected(self):
        response = await self.client.post(
            "/api/evaluate", json={"target": "ollama", "url": "https://example.invalid"}
        )
        self.assertEqual(response.status_code, 422)

    async def test_invalid_local_model_rejected(self):
        response = await self.client.post("/api/evaluate", json={"target": "ollama", "model": "../../"})
        self.assertEqual(response.status_code, 422)

    async def test_unavailable_ollama_has_actionable_error(self):
        with patch("redteam_eval.web.OllamaTarget.verify_ready", side_effect=TargetError("Start Ollama, then retry.")):
            response = await self.client.post("/api/evaluate", json={"target": "ollama"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("Start Ollama", response.json()["detail"])

    async def test_ollama_reports_local_model_and_separate_case_errors(self):
        outcomes = ["A public summary."] * 7 + [TargetError("Local model timed out.")]
        with (
            patch("redteam_eval.web.OllamaTarget.verify_ready") as verify,
            patch("redteam_eval.web.OllamaTarget.complete", side_effect=outcomes) as complete,
        ):
            response = await self.client.post("/api/evaluate", json={"target": "ollama", "model": "demo:3b"})
        self.assertEqual(response.status_code, 200)
        report = response.json()
        self.assertEqual(report["execution"]["mode"], "local_model")
        self.assertEqual(report["execution"]["model"], "demo:3b")
        self.assertEqual(report["results"]["summary"]["evaluated"], 7)
        self.assertEqual(report["results"]["summary"]["errors"], 1)
        self.assertIn("timed out", report["results"]["findings"][-1]["response_preview"])
        verify.assert_called_once()
        self.assertEqual(complete.call_count, 8)

    async def test_home_and_static_assets(self):
        home = await self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("script-src 'self'", home.headers["content-security-policy"])
        self.assertIn("AI Red-Teaming &amp; LLM Security Evaluation Framework", home.text)
        self.assertIn('rel="icon" href="/favicon.ico"', home.text)
        icon = await self.client.get("/favicon.ico")
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(icon.headers["content-type"], "image/vnd.microsoft.icon")
        self.assertTrue(icon.content.startswith(b"\x00\x00\x01\x00"))
        for asset in ("styles.css", "app.js"):
            with self.subTest(asset=asset):
                self.assertEqual((await self.client.get(f"/static/{asset}")).status_code, 200)

    async def test_openapi_describes_evaluation_endpoints(self):
        response = await self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        paths = json.loads(response.text)["paths"]
        self.assertIn("post", paths["/api/evaluate"])
        self.assertIn("get", paths["/api/health"])
        self.assertIn("get", paths["/api/scenarios"])


if __name__ == "__main__":
    unittest.main()
