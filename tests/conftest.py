"""Shared fixtures: a deterministic Video Part factory and a fake Gemini API server.

The fake server implements the subset of the Gemini REST protocol used by the
``google-genai`` SDK (Files API init, resumable finalize, files.get, files.delete
and models.generateContent). That lets the integration tests exercise the real
SDK serialization, real HTTP calls and the real cleanup path with no network.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest
from google.genai import types

from schemas import TestStep, TestCase, TestSuite

VALID_SUITE_JSON: dict[str, Any] = {
    "feature_summary": "Promo code redemption on checkout; admins manage codes while shoppers apply them.",
    "presenter_business_rules": [
        "Promo codes expire after 30 days.",
        "Only 3 failed redemptions are allowed per session.",
    ],
    "test_cases": [
        {
            "id": "TC-001",
            "title": "Apply a valid promo code and confirm the discounted total",
            "category": "Happy Path",
            "priority": "High",
            "video_timestamp": "00:12-00:41",
            "source_basis": "Observed in UI",
            "preconditions": ["Shopper is logged in", "Cart contains one item"],
            "steps": [
                {"step_number": 1, "action": "Open the checkout page", "test_data": "", "expected_result": "Checkout page loads with the promo field visible"},
                {"step_number": 2, "action": "Enter promo code SUMMER25", "test_data": "SUMMER25", "expected_result": "Code is accepted and the total drops by 25%"},
            ],
        },
        {
            "id": "TC-002",
            "title": "Reject an expired promo code",
            "category": "Negative",
            "priority": "High",
            "video_timestamp": "00:42-01:05",
            "source_basis": "Spoken by Presenter",
            "preconditions": [],
            "steps": [
                {"step_number": 1, "action": "Enter promo code OLD10", "test_data": "OLD10", "expected_result": "Toast shows the code has expired"},
            ],
        },
        {
            "id": "TC-003",
            "title": "Block a fourth failed redemption attempt in one session",
            "category": "Boundary",
            "priority": "Medium",
            "video_timestamp": "01:06-01:31",
            "source_basis": "Spoken by Presenter",
            "preconditions": ["Three failed redemptions already happened this session"],
            "steps": [
                {"step_number": 1, "action": "Submit any invalid promo code", "test_data": "BADC0DE", "expected_result": "The promo field is locked and an error banner appears"},
            ],
        },
    ],
}


def make_video_bytes() -> bytes:
    """Small but non-empty bytes; slides have no ffmpeg, so mime comes from the name."""

    return b"\x00\x00\x00\x18ftypmp42" + b"walkthrough" * 32


def sample_suite() -> TestSuite:
    return TestSuite(
        feature_summary=(
            "Checkout promo codes let shoppers redeem discounts while admins curate codes."
        ),
        presenter_business_rules=[
            "Promo codes expire after 30 days.",
            "Only 3 failed redemptions are allowed per session.",
        ],
        test_cases=[
            TestCase(
                id="TC-001",
                title="Apply a valid promo code",
                category="Happy Path",
                priority="High",
                video_timestamp="00:12-00:41",
                source_basis="Observed in UI",
                preconditions=["Shopper is logged in"],
                steps=[
                    TestStep(step_number=1, action="Open checkout", test_data="", expected_result="Checkout loads"),
                    TestStep(step_number=2, action="Enter SUMMER25", test_data="SUMMER25", expected_result="Total drops"),
                ],
            ),
            TestCase(
                id="TC-002",
                title="Reject an expired promo code",
                category="Negative",
                priority="High",
                video_timestamp="00:42-01:05",
                source_basis="Spoken by Presenter",
                preconditions=[],
                steps=[
                    TestStep(step_number=1, action="Enter OLD10", test_data="OLD10", expected_result="Expired toast"),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Fake Gemini API server
# ---------------------------------------------------------------------------
@dataclass
class FakeGeminiServer:
    """Threaded HTTP server emulating the Gemini API endpoints used by the app."""

    response_json: Any = field(default_factory=lambda: json.loads(json.dumps(VALID_SUITE_JSON)))
    states: list[str] = field(default_factory=lambda: ["ACTIVE"])
    fail_message: str = "The video format is not supported."
    generate_status_code: int = 200
    upload_status: int = 200
    processed_states: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)
    generated_body: dict[str, Any] | None = None
    deleted: bool = False
    uploaded_bytes: bytes = b""
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/"

    def start(self) -> FakeGeminiServer:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence test output
                return

            # -- helpers --------------------------------------------------
            def _read_body(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _send_json(self, code: int, payload: Any, extra: dict[str, str] | None = None) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def _record(self, method: str, body: bytes = b"") -> None:
                outer.requests.append(
                    {
                        "method": method,
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )

            def _file_fields(self, state: str) -> dict[str, Any]:
                return {
                    "name": "files/mock-video-123",
                    "mimeType": "video/mp4",
                    "sizeBytes": str(len(outer.uploaded_bytes) or 1),
                    "state": state,
                    "uri": "https://generativelanguage.googleapis.com/v1beta/files/mock-video-123",
                    "displayName": "walkthrough.mp4",
                }

            def _file_payload(self, state: str) -> dict[str, Any]:
                # The resumable-upload finalize response wraps the file in {"file": ...};
                # files.get returns the File object directly.
                return {"file": self._file_fields(state)}

            # -- routes ---------------------------------------------------
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                body = self._read_body()
                self._record("POST", body)
                if self.path.startswith("/upload/") and "upload_session" not in self.path:
                    if outer.upload_status >= 400:
                        self._send_json(
                            outer.upload_status,
                            {"error": {"code": outer.upload_status, "status": "INVALID_ARGUMENT", "message": "bad upload"}},
                        )
                        return
                    self._send_json(200, {}, {"x-goog-upload-url": f"{outer.base_url}upload_session"})
                    return
                if self.path.startswith("/upload_session"):
                    outer.uploaded_bytes += body
                    outer.processed_states = 0
                    state = outer.states[0] if outer.states else "ACTIVE"
                    self._send_json(200, self._file_payload(state), {"x-goog-upload-status": "final"})
                    return
                if ":generateContent" in self.path:
                    outer.generated_body = json.loads(body) if body else None
                    if outer.generate_status_code >= 400:
                        self._send_json(
                            outer.generate_status_code,
                            {"error": {"code": outer.generate_status_code, "status": "RESOURCE_EXHAUSTED", "message": "quota exceeded"}},
                        )
                        return
                    self._send_json(
                        200,
                        {
                            "candidates": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": json.dumps(outer.response_json)}],
                                    },
                                    "finishReason": "STOP",
                                }
                            ],
                            "usageMetadata": {
                                "promptTokenCount": 12345,
                                "candidatesTokenCount": 678,
                                "thoughtsTokenCount": 90,
                                "totalTokenCount": 13113,
                            },
                        },
                    )
                    return
                self._send_json(404, {"error": {"code": 404, "message": f"no route {self.path}"}})

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                self._record("GET")
                if self.path.startswith("/v1beta/files/"):
                    # 1st get -> states[1], 2nd -> states[2], ... clamped to the last entry
                    outer.processed_states += 1
                    index = min(outer.processed_states, max(len(outer.states) - 1, 0))
                    state = outer.states[index] if outer.states else "ACTIVE"
                    payload = self._file_fields(state)
                    if state == "FAILED":
                        payload["error"] = {"code": 400, "message": outer.fail_message}
                    self._send_json(200, payload)
                    return
                self._send_json(404, {"error": {"code": 404, "message": "not found"}})

            def do_DELETE(self) -> None:  # noqa: N802 - http.server API
                self._record("DELETE")
                if self.path.startswith("/v1beta/files/"):
                    outer.deleted = True
                    self._send_json(200, {})
                    return
                self._send_json(404, {"error": {"code": 404, "message": "not found"}})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._server = server
        self._thread = thread
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def paths(self, method: str | None = None) -> list[str]:
        return [
            request["path"]
            for request in self.requests
            if method is None or request["method"] == method
        ]


@pytest.fixture
def fake_gemini() -> Iterator[FakeGeminiServer]:
    server = FakeGeminiServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    path = tmp_path / "walkthrough.mp4"
    path.write_bytes(make_video_bytes())
    return path


@pytest.fixture
def mock_types() -> Any:
    return types
