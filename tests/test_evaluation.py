from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import respx

from scrape_gateway.config import EvaluationConfig
from scrape_gateway.models import FailureReason, ScrapeRequest, ScrapeResult
from scrape_gateway.provider import ProviderAdapter


GOOD_JUDGMENT = {
    "verdict": "pass",
    "needs_human_review": False,
    "checks": {
        "access": {
            "result": "pass",
            "evidence": "The page loaded without an access block.",
        },
        "goal_coverage": {
            "result": "pass",
            "evidence": "Visible product names and prices satisfy the caller goal.",
        },
        "extractability": {
            "result": "pass",
            "evidence": "Product names and prices are present in Markdown.",
        },
        "visual_state": {
            "result": "pass",
            "evidence": "The screenshot shows a loaded product grid.",
        },
    },
    "page_type": "product listing",
    "root_cause": "none",
    "issues": [],
    "recommended_action": "accept",
    "improvement_opportunities": [],
    "critique": "The captured page is usable for extracting the visible product listing.",
}


def test_strict_schema_uses_binary_verdict_and_categorical_checks() -> None:
    from scrape_gateway.evaluation import ScrapeQualityEvaluation

    schema = ScrapeQualityEvaluation.model_json_schema()
    assert schema["properties"]["verdict"]["enum"] == ["pass", "fail"]
    assert "needs_human_review" in schema["required"]
    assert "confidence" not in schema["properties"]
    assert "scores" not in schema["properties"]

    checks_schema = schema["$defs"]["QualityChecks"]
    assert set(checks_schema["required"]) == {
        "access",
        "goal_coverage",
        "extractability",
        "visual_state",
    }
    check_schema = schema["$defs"]["QualityCheck"]
    assert check_schema["properties"]["result"]["enum"] == [
        "pass",
        "fail",
        "not_applicable",
    ]


@respx.mock
async def test_openrouter_evaluator_sends_strict_schema_and_image(tmp_path: Path) -> None:
    from scrape_gateway.evaluation import OpenRouterEvaluator

    completion = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gen-test-123",
                "model": "google/gemini-3.1-flash-lite",
                "provider": "Google",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(GOOD_JUDGMENT),
                        },
                        "finish_reason": "stop",
                        "native_finish_reason": "STOP",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": 120,
                    "total_tokens": 1320,
                    "cost": 0.00048,
                    "is_byok": True,
                },
            },
        )
    )
    generation = respx.get("https://openrouter.ai/api/v1/generation").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "gen-test-123",
                    "provider_name": "Google Vertex",
                    "is_byok": True,
                    "latency": 390,
                    "generation_time": 350,
                    "total_cost": 0.00048,
                    "tokens_prompt": 1200,
                    "tokens_completion": 120,
                }
            },
        )
    )

    evaluator = OpenRouterEvaluator(
        EvaluationConfig(
            mode="audit",
            include_screenshot=True,
            cache_root=str(tmp_path / "evaluation-cache"),
        ),
        api_key="test-openrouter-key",
    )
    outcome = await evaluator.evaluate(
        request=ScrapeRequest(
            "https://example.com/products",
            render_js=True,
            metadata={"evaluation_goal": "Extract the visible product listing"},
        ),
        result=ScrapeResult(
            url="https://example.com/products",
            provider="browserless",
            success=True,
            status_code=200,
            markdown="# Products\n\nWidget — $19.99",
            screenshot=b"\x89PNG\r\n\x1a\nimage-bytes",
            content_validated=True,
            route="browserless:content+screenshot",
        ),
        attempts=[{"provider": "browserless", "result": "success"}],
        elapsed_ms=750,
    )

    assert outcome.status == "completed"
    assert outcome.judgment == GOOD_JUDGMENT
    assert outcome.generation_id == "gen-test-123"
    assert outcome.provider == "Google Vertex"
    assert outcome.usage["cost"] == 0.00048
    assert outcome.response_metadata["generation"]["provider_name"] == "Google Vertex"
    assert outcome.response_metadata["generation"]["is_byok"] is True
    assert outcome.response_metadata["choice_metadata"] == [
        {
            "finish_reason": "stop",
            "native_finish_reason": "STOP",
            "message": {"role": "assistant"},
        }
    ]

    payload = json.loads(completion.calls[0].request.content)
    assert completion.calls[0].request.headers["X-OpenRouter-Title"] == "scrape-gateway"
    assert payload["model"] == "google/gemini-3.1-flash-lite"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert payload["usage"] == {"include": True}

    user_content = payload["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert "BEGIN UNTRUSTED MARKDOWN EVIDENCE" in user_content[0]["text"]
    assert "Extract the visible product listing" in user_content[0]["text"]
    assert "mark the visual-state check not_applicable" in user_content[0]["text"]
    assert "visual score" not in user_content[0]["text"]
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert generation.calls[0].request.url.params["id"] == "gen-test-123"


@respx.mock
async def test_generation_metadata_retries_eventual_404(
    tmp_path: Path, monkeypatch
) -> None:
    from scrape_gateway.evaluation import OpenRouterEvaluator

    generation = respx.get("https://openrouter.ai/api/v1/generation").mock(
        side_effect=[
            httpx.Response(404, json={"error": {"message": "not ready"}}),
            httpx.Response(
                200,
                json={
                    "data": {
                        "id": "gen-eventual",
                        "provider_name": "Google Vertex",
                        "is_byok": True,
                    }
                },
            ),
        ]
    )
    delays = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    evaluator = OpenRouterEvaluator(
        EvaluationConfig(mode="audit", cache_root=str(tmp_path / "cache")),
        api_key="test-openrouter-key",
    )

    metadata = await evaluator._generation_metadata("gen-eventual")

    assert metadata["provider_name"] == "Google Vertex"
    assert metadata["is_byok"] is True
    assert generation.call_count == 2
    assert delays == [0.5]


@respx.mock
async def test_evaluator_records_only_evidence_modalities_that_are_present(
    tmp_path: Path,
) -> None:
    from scrape_gateway.evaluation import OpenRouterEvaluator

    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "google/gemini-3.1-flash-lite",
                "choices": [{"message": {"content": json.dumps(GOOD_JUDGMENT)}}],
                "usage": {"total_tokens": 500, "cost": 0.0002},
            },
        )
    )
    evaluator = OpenRouterEvaluator(
        EvaluationConfig(
            mode="audit",
            include_screenshot=True,
            cache_root=str(tmp_path / "cache"),
        ),
        api_key="test-openrouter-key",
    )

    outcome = await evaluator.evaluate(
        request=ScrapeRequest("https://example.com", screenshot=True),
        result=ScrapeResult(
            url="https://example.com",
            provider="screenshot-only",
            success=True,
            screenshot=b"\x89PNG\r\n\x1a\nimage-bytes",
        ),
        attempts=[],
        elapsed_ms=100,
    )

    assert outcome.input_modalities == ["screenshot"]


@respx.mock
async def test_evaluator_reuses_content_hash_cache(tmp_path: Path) -> None:
    from scrape_gateway.evaluation import OpenRouterEvaluator

    completion = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gen-cache-source",
                "model": "google/gemini-3.1-flash-lite",
                "provider": "Google",
                "choices": [{"message": {"content": json.dumps(GOOD_JUDGMENT)}}],
                "usage": {"total_tokens": 600, "cost": 0.0002},
            },
        )
    )
    evaluator = OpenRouterEvaluator(
        EvaluationConfig(
            mode="audit",
            include_screenshot=False,
            cache_root=str(tmp_path / "evaluation-cache"),
        ),
        api_key="test-openrouter-key",
    )
    request = ScrapeRequest("https://example.com/products")
    result = ScrapeResult(
        url=request.url,
        provider="raw_http",
        success=True,
        status_code=200,
        markdown="# Products\n\nWidget — $19.99",
        content_validated=True,
    )

    first = await evaluator.evaluate(
        request=request,
        result=result,
        attempts=[
            {
                "provider": "raw_http",
                "result": "success",
                "elapsed_ms": 100,
                "artifact_path": "/runs/first/failed.html",
            }
        ],
        elapsed_ms=100,
    )
    second = await evaluator.evaluate(
        request=request,
        result=result,
        attempts=[
            {
                "provider": "raw_http",
                "result": "success",
                "elapsed_ms": 999,
                "artifact_path": "/runs/second/failed.html",
            }
        ],
        elapsed_ms=999,
    )

    assert first.cached is False
    assert second.cached is True
    assert second.judgment == GOOD_JUDGMENT
    assert second.usage["cost"] == 0
    assert second.response_metadata["cache_source_usage"]["cost"] == 0.0002
    assert completion.call_count == 1


@respx.mock
async def test_evaluation_cache_distinguishes_provider_context(tmp_path: Path) -> None:
    from scrape_gateway.evaluation import OpenRouterEvaluator

    completion = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gen-provider-context",
                "model": "google/gemini-3.1-flash-lite",
                "choices": [{"message": {"content": json.dumps(GOOD_JUDGMENT)}}],
                "usage": {"total_tokens": 600, "cost": 0.0002},
            },
        )
    )
    evaluator = OpenRouterEvaluator(
        EvaluationConfig(
            mode="audit",
            include_screenshot=False,
            cache_root=str(tmp_path / "evaluation-cache"),
        ),
        api_key="test-openrouter-key",
    )
    request = ScrapeRequest("https://example.com/products")

    for provider in ("raw_http", "browserless"):
        await evaluator.evaluate(
            request=request,
            result=ScrapeResult(
                url=request.url,
                provider=provider,
                route=provider,
                success=True,
                status_code=200,
                markdown="# Products\n\nWidget — $19.99",
                content_validated=True,
            ),
            attempts=[{"provider": provider, "result": "success"}],
            elapsed_ms=100,
        )

    assert completion.call_count == 2


@respx.mock
async def test_openrouter_error_is_persistable_without_raising(tmp_path: Path) -> None:
    from scrape_gateway.evaluation import OpenRouterEvaluator

    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            503,
            json={"error": {"message": "upstream model temporarily unavailable"}},
            headers={"x-request-id": "req-eval-503"},
        )
    )
    evaluator = OpenRouterEvaluator(
        EvaluationConfig(mode="audit", cache_root=str(tmp_path / "cache")),
        api_key="test-openrouter-key",
    )

    outcome = await evaluator.evaluate(
        request=ScrapeRequest("https://example.com"),
        result=ScrapeResult(
            url="https://example.com",
            provider="raw_http",
            success=True,
            status_code=200,
            markdown="# Example\n\nUseful page content.",
        ),
        attempts=[],
        elapsed_ms=100,
    )

    assert outcome.status == "failed"
    assert outcome.response_metadata["http_status"] == 503
    assert outcome.response_metadata["request_id"] == "req-eval-503"
    assert "upstream model temporarily unavailable" in outcome.response_metadata["body"]


@respx.mock
async def test_cache_write_failure_keeps_completed_judgment(tmp_path: Path) -> None:
    from scrape_gateway.evaluation import OpenRouterEvaluator

    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gen-cache-write-failure",
                "model": "google/gemini-3.1-flash-lite",
                "choices": [{"message": {"content": json.dumps(GOOD_JUDGMENT)}}],
                "usage": {"cost": 0.0002, "total_tokens": 600},
            },
        )
    )
    cache_parent = tmp_path / "not-a-directory"
    cache_parent.write_text("occupied")
    evaluator = OpenRouterEvaluator(
        EvaluationConfig(mode="audit", cache_root=str(cache_parent / "cache")),
        api_key="test-openrouter-key",
    )

    outcome = await evaluator.evaluate(
        request=ScrapeRequest("https://example.com"),
        result=ScrapeResult(
            url="https://example.com",
            provider="raw_http",
            success=True,
            status_code=200,
            markdown="# Example\n\nUseful page content.",
        ),
        attempts=[],
        elapsed_ms=100,
    )

    assert outcome.status == "completed"
    assert outcome.judgment == GOOD_JUDGMENT
    assert "cache_write_error" in outcome.response_metadata


def test_telemetry_persists_complete_evaluation_bundle(tmp_path: Path) -> None:
    from scrape_gateway.evaluation import EvaluationOutcome
    from scrape_gateway.telemetry import TelemetryRecorder

    outcome = EvaluationOutcome(
        status="completed",
        model="google/gemini-3.1-flash-lite",
        judgment=GOOD_JUDGMENT,
        generation_id="gen-test-123",
        provider="Google",
        usage={"prompt_tokens": 1200, "completion_tokens": 120, "cost": 0.00048},
        elapsed_ms=420,
        input_modalities=["markdown", "screenshot"],
        markdown_evidence="# Products\n\nWidget — $19.99",
        request_payload={
            "model": "google/gemini-3.1-flash-lite",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Evaluate this scrape"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,very-large-value"},
                        },
                    ],
                }
            ],
        },
        response_metadata={"id": "gen-test-123", "provider": "Google"},
    )
    result = ScrapeResult(
        url="https://example.com/products",
        provider="browserless",
        success=True,
        html="<html><body><h1>Products</h1><p>Widget — $19.99</p></body></html>",
        markdown="# Products\n\nWidget — $19.99",
        screenshot=b"\x89PNG\r\n\x1a\nimage-bytes",
    )

    recorder = TelemetryRecorder(root=tmp_path / "runs")
    paths = recorder.write_evaluation_artifacts("run-123", outcome, result)

    evaluation_dir = tmp_path / "runs" / "run-123" / "evaluation"
    assert Path(paths["input_markdown"]).read_text() == outcome.markdown_evidence
    assert Path(paths["response"]).read_text()
    assert Path(paths["screenshot"]).read_bytes() == result.screenshot
    assert Path(paths["final_html"]).read_text() == result.html
    assert Path(paths["final_markdown"]).read_text() == result.markdown

    saved_request = json.loads((evaluation_dir / "request.json").read_text())
    image_url = saved_request["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url == "<saved separately: screenshot.png>"

    metadata = json.loads((evaluation_dir / "metadata.json").read_text())
    assert metadata["prompt_version"] == "scrape-usability-v2"
    assert metadata["generation_id"] == "gen-test-123"
    assert metadata["usage"]["cost"] == 0.00048
    assert metadata["content_hashes"]["markdown"]
    assert metadata["content_hashes"]["screenshot"]


def test_telemetry_redacts_unknown_base64_screenshot_from_request_json(tmp_path: Path) -> None:
    from scrape_gateway.evaluation import EvaluationOutcome
    from scrape_gateway.telemetry import TelemetryRecorder

    outcome = EvaluationOutcome(
        status="failed",
        model="google/gemini-3.1-flash-lite",
        request_payload={
            "messages": [
                {
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:application/octet-stream;base64,very-large-value"
                            },
                        }
                    ]
                }
            ]
        },
    )
    result = ScrapeResult(
        url="https://example.com",
        provider="unknown-image-provider",
        success=False,
        screenshot=b"unknown-image-bytes",
    )

    paths = TelemetryRecorder(root=tmp_path / "runs").write_evaluation_artifacts(
        "run-unknown-image", outcome, result
    )

    saved = json.loads(Path(paths["request"]).read_text())
    assert saved["messages"][0]["content"][0]["image_url"]["url"] == (
        "<saved separately: screenshot.bin>"
    )


def test_request_summary_keeps_quality_relevant_request_context() -> None:
    from scrape_gateway.telemetry import request_summary

    summary = request_summary(
        ScrapeRequest(
            "https://example.com",
            screenshot=True,
            extra_wait_ms=750,
            block_ads=True,
            skip_validation=True,
            referer="https://search.example/",
            metadata={
                "evaluation_goal": "Capture product cards",
                "start_tier": "scrapedrive:advanced",
                "token": "must-not-leak",
            },
        ),
        use_cache=False,
        use_memory=True,
        proxy_enabled=False,
    )

    assert summary["screenshot"] is True
    assert summary["extra_wait_ms"] == 750
    assert summary["block_ads"] is True
    assert summary["skip_validation"] is True
    assert summary["referer"] == "https://search.example/"
    assert summary["metadata"]["start_tier"] == "scrapedrive:advanced"
    assert summary["metadata"]["token"] == "<redacted>"


async def test_gateway_audit_evaluation_is_saved_without_changing_success(tmp_path: Path) -> None:
    from scrape_gateway.cache import ArtifactCache
    from scrape_gateway.evaluation import EvaluationOutcome
    from scrape_gateway.memory import DomainMemory
    from scrape_gateway.router import ScrapeGateway
    from scrape_gateway.telemetry import TelemetryRecorder

    class EvaluatedProvider(ProviderAdapter):
        name = "evaluated"
        capabilities = frozenset({"html"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                html=(
                    "<html><body><h1>Products</h1>"
                    "<p>Widget listing content with enough text for deterministic validation.</p>"
                    "</body></html>"
                ),
                route="evaluated",
            )

    class FakeEvaluator:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, **kwargs) -> EvaluationOutcome:
            self.calls += 1
            return EvaluationOutcome(
                status="completed",
                model="google/gemini-3.1-flash-lite",
                judgment=GOOD_JUDGMENT,
                generation_id="gen-gateway-test",
                provider="Google",
                usage={"cost": 0.0003, "total_tokens": 800},
                input_modalities=["markdown"],
                markdown_evidence=kwargs["result"].markdown or "",
                request_payload={"model": "google/gemini-3.1-flash-lite"},
            )

    evaluator = FakeEvaluator()
    recorder = TelemetryRecorder(root=tmp_path / "runs")
    gateway = ScrapeGateway(
        providers=[EvaluatedProvider()],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=DomainMemory(db_path=tmp_path / "memory.sqlite"),
        telemetry=recorder,
        evaluator=evaluator,
    )

    result = await gateway.scrape(
        ScrapeRequest("https://example.com/products"),
        use_cache=False,
        use_memory=False,
    )

    assert result.success is True
    assert evaluator.calls == 1
    assert result.metadata["evaluation"]["verdict"] == "pass"

    report = json.loads(Path(result.metadata["telemetry_report"]).read_text())
    assert report["evaluation"]["status"] == "completed"
    assert report["evaluation"]["verdict"] == "pass"
    assert report["evaluation"]["page_type"] == "product listing"
    assert report["evaluation"]["usage"]["cost"] == 0.0003
    assert Path(report["evaluation"]["artifacts"]["response"]).exists()
    assert Path(report["evaluation"]["artifacts"]["final_html"]).exists()


async def test_audit_mode_preserves_failed_attempt_html_for_improvement_analysis(
    tmp_path: Path,
) -> None:
    from scrape_gateway.cache import ArtifactCache
    from scrape_gateway.evaluation import EvaluationOutcome
    from scrape_gateway.memory import DomainMemory
    from scrape_gateway.router import ScrapeGateway
    from scrape_gateway.telemetry import TelemetryRecorder

    class BlockedProvider(ProviderAdapter):
        name = "blocked"
        cost_rank = 1
        capabilities = frozenset({"html"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                html="<html><body>Checking your browser. Ray ID: abc</body></html>" + "x" * 200,
                screenshot=b"\x89PNG\r\n\x1a\nblocked-page",
                route="blocked",
            )

    class RecoveryProvider(ProviderAdapter):
        name = "recovery"
        cost_rank = 10
        capabilities = frozenset({"html"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                html=(
                    "<html><body><h1>Recovered</h1>"
                    "<p>Useful recovered content with enough text for validation.</p>"
                    "</body></html>"
                ),
                route="recovery",
            )

    class FakeEvaluator:
        async def evaluate(self, **kwargs) -> EvaluationOutcome:
            return EvaluationOutcome(
                status="completed",
                model="google/gemini-3.1-flash-lite",
                judgment=GOOD_JUDGMENT,
                markdown_evidence=kwargs["result"].markdown or "",
            )

    gateway = ScrapeGateway(
        providers=[BlockedProvider(), RecoveryProvider()],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=DomainMemory(db_path=tmp_path / "memory.sqlite"),
        telemetry=TelemetryRecorder(root=tmp_path / "runs"),
        evaluator=FakeEvaluator(),
    )
    result = await gateway.scrape(
        ScrapeRequest("https://example.com"),
        use_cache=False,
        use_memory=False,
    )

    report = json.loads(Path(result.metadata["telemetry_report"]).read_text())
    failed_attempt = report["attempts"][0]
    assert failed_attempt["result"] == "validation_failed"
    artifact_path = Path(failed_attempt["artifact_path"])
    assert artifact_path.exists()
    assert "Checking your browser" in artifact_path.read_text()
    screenshot_path = Path(failed_attempt["screenshot_artifact_path"])
    assert screenshot_path.exists()
    assert screenshot_path.read_bytes().endswith(b"blocked-page")


async def test_evaluator_exception_never_changes_scrape_success(tmp_path: Path) -> None:
    from scrape_gateway.cache import ArtifactCache
    from scrape_gateway.memory import DomainMemory
    from scrape_gateway.router import ScrapeGateway
    from scrape_gateway.telemetry import TelemetryRecorder

    class HealthyProvider(ProviderAdapter):
        name = "healthy"
        capabilities = frozenset({"html"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                html=(
                    "<html><body><h1>Useful page</h1>"
                    "<p>Enough meaningful content to pass deterministic validation.</p>"
                    "</body></html>"
                ),
            )

    class ExplodingEvaluator:
        async def evaluate(self, **kwargs):
            raise RuntimeError("simulated evaluator outage")

    gateway = ScrapeGateway(
        providers=[HealthyProvider()],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=DomainMemory(db_path=tmp_path / "memory.sqlite"),
        telemetry=TelemetryRecorder(root=tmp_path / "runs"),
        evaluator=ExplodingEvaluator(),
    )

    result = await gateway.scrape(
        ScrapeRequest("https://example.com/useful"),
        use_cache=False,
        use_memory=False,
    )

    assert result.success is True
    assert result.metadata["evaluation"]["status"] == "failed"
    report = json.loads(Path(result.metadata["telemetry_report"]).read_text())
    assert report["evaluation"]["status"] == "failed"
    assert "simulated evaluator outage" in report["evaluation"]["error"]


async def test_audit_mode_preserves_failed_screenshot_without_html(tmp_path: Path) -> None:
    from scrape_gateway.cache import ArtifactCache
    from scrape_gateway.evaluation import EvaluationOutcome
    from scrape_gateway.memory import DomainMemory
    from scrape_gateway.router import ScrapeGateway
    from scrape_gateway.telemetry import TelemetryRecorder

    class FailedScreenshotProvider(ProviderAdapter):
        name = "failed-screenshot"
        capabilities = frozenset({"screenshot"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                status_code=502,
                screenshot=b"\x89PNG\r\n\x1a\nfailed-screenshot",
                failure_reason=FailureReason.PROVIDER_ERROR,
                error="content endpoint failed",
            )

    class FakeEvaluator:
        async def evaluate(self, **kwargs) -> EvaluationOutcome:
            return EvaluationOutcome(
                status="completed",
                model="google/gemini-3.1-flash-lite",
                judgment=GOOD_JUDGMENT,
            )

    gateway = ScrapeGateway(
        providers=[FailedScreenshotProvider()],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=DomainMemory(db_path=tmp_path / "memory.sqlite"),
        telemetry=TelemetryRecorder(root=tmp_path / "runs"),
        evaluator=FakeEvaluator(),
    )

    result = await gateway.scrape(
        ScrapeRequest("https://example.com", screenshot=True),
        use_cache=False,
        use_memory=False,
    )

    report = json.loads(Path(result.metadata["telemetry_report"]).read_text())
    screenshot_path = Path(report["attempts"][0]["screenshot_artifact_path"])
    assert screenshot_path.read_bytes().endswith(b"failed-screenshot")


def test_summarize_evaluations_surfaces_recurring_improvements() -> None:
    from scrape_gateway.telemetry import summarize_evaluations

    reports = [
        {
            "run_id": "run-fail",
            "url": "https://shop.example/products",
            "domain": "shop.example",
            "_path": "/runs/run-fail/report.json",
            "evaluation": {
                "status": "completed",
                "model": "google/gemini-3.1-flash-lite",
                "provider": "Google Vertex",
                "prompt_version": "scrape-usability-v2",
                "verdict": "fail",
                "needs_human_review": False,
                "page_type": "product listing",
                "root_cause": "render_failure",
                "recommended_action": "retry_with_wait",
                "checks": {
                    "access": {
                        "result": "pass",
                        "evidence": "The page is accessible.",
                    },
                    "goal_coverage": {
                        "result": "fail",
                        "evidence": "The product grid is incomplete.",
                    },
                    "extractability": {
                        "result": "fail",
                        "evidence": "Only loading placeholders were extracted.",
                    },
                    "visual_state": {
                        "result": "fail",
                        "evidence": "The screenshot shows a loading state.",
                    },
                },
                "issues": [
                    {
                        "code": "loading_state",
                        "severity": "high",
                        "source": "screenshot",
                        "evidence": "Products are still loading.",
                    }
                ],
                "improvement_opportunities": ["Wait for the product grid selector."],
                "usage": {
                    "cost": 0.0004,
                    "total_tokens": 1000,
                    "cost_details": {"upstream_inference_cost": 0.0005},
                },
                "cached": False,
            },
        },
        {
            "run_id": "run-pass",
            "url": "https://shop.example/about",
            "domain": "shop.example",
            "_path": "/runs/run-pass/report.json",
            "evaluation": {
                "status": "completed",
                "model": "google/gemini-3.1-flash-lite",
                "provider": "Google Vertex",
                "prompt_version": "scrape-usability-v2",
                "verdict": "pass",
                "needs_human_review": False,
                "page_type": "about page",
                "root_cause": "none",
                "recommended_action": "accept",
                "checks": {
                    "access": {
                        "result": "pass",
                        "evidence": "The page is accessible.",
                    },
                    "goal_coverage": {
                        "result": "pass",
                        "evidence": "The about content is complete.",
                    },
                    "extractability": {
                        "result": "pass",
                        "evidence": "The about content is present in Markdown.",
                    },
                    "visual_state": {
                        "result": "not_applicable",
                        "evidence": "No screenshot was supplied.",
                    },
                },
                "issues": [],
                "improvement_opportunities": [
                    "The current extraction is sufficient; no improvements needed."
                ],
                "usage": {"cost": 0, "total_tokens": 0, "cache_hit": True},
                "cached": True,
            },
        },
        {
            "run_id": "run-failed-eval",
            "url": "https://other.example/",
            "domain": "other.example",
            "_path": "/runs/run-failed-eval/report.json",
            "evaluation": {
                "status": "failed",
                "model": "google/gemini-3.1-flash-lite",
                "provider": None,
                "usage": {},
                "error": "upstream timeout",
            },
        },
    ]

    summary = summarize_evaluations(reports)

    assert summary["runs_scanned"] == 3
    assert summary["evaluated_runs"] == 3
    assert summary["status_counts"] == {"completed": 2, "failed": 1}
    assert summary["verdict_counts"] == {"fail": 1, "pass": 1}
    assert summary["page_type_counts"] == {"about page": 1, "product listing": 1}
    assert summary["issue_counts"] == {"loading_state": 1}
    assert summary["root_cause_counts"] == {"none": 1, "render_failure": 1}
    assert summary["recommended_action_counts"] == {"accept": 1, "retry_with_wait": 1}
    assert summary["improvement_opportunities"] == [
        {"text": "Wait for the product grid selector.", "count": 1}
    ]
    assert summary["check_failure_counts"] == {
        "extractability": 1,
        "goal_coverage": 1,
        "visual_state": 1,
    }
    assert summary["check_result_counts"]["visual_state"] == {
        "fail": 1,
        "not_applicable": 1,
    }
    assert summary["prompt_version_counts"] == {"scrape-usability-v2": 2}
    assert summary["usage"] == {
        "cost": 0.0004,
        "upstream_inference_cost": 0.0005,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 1000,
        "cached_runs": 1,
    }
    assert [item["run_id"] for item in summary["review_queue"]] == [
        "run-fail",
        "run-failed-eval",
    ]
    assert summary["calibration_status"] == "uncalibrated_audit"
