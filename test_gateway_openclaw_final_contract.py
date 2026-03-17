from pathlib import Path


def test_openclaw_gateway_waits_for_final_after_ack() -> None:
    source = Path("orchestrator/gateway/provider_backends/core.py").read_text(encoding="utf-8")

    assert "expect_final: bool = False" in source
    assert "self._pending_requests[request_id] = (fut, expect_final)" in source
    assert "if expect_final:" in source
    assert "if status == \"accepted\":" in source
    assert "continue" in source


def test_openclaw_agent_requests_expect_final() -> None:
    source = Path("orchestrator/gateway/provider_backends/core.py").read_text(encoding="utf-8")

    assert "res = await self._send_request(\"agent\", payload, timeout_s=self.timeout_s, expect_final=True)" in source


if __name__ == "__main__":
    test_openclaw_gateway_waits_for_final_after_ack()
    test_openclaw_agent_requests_expect_final()
    print("openclaw gateway final contract: ok")
