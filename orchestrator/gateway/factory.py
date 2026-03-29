from orchestrator.config import VoiceConfig
from orchestrator.gateway.providers import (
    BaseGateway,
    GenericGateway,
    OpenClawGateway,
    ZeroClawGateway,
    TinyClawGateway,
    IronClawGateway,
    MimiClawGateway,
    PicoClawGateway,
    NanoBotGateway,
)


def build_gateway(config: VoiceConfig) -> BaseGateway:
    provider = (config.gateway_provider or "openclaw").lower()
    timeout_s = max(1, int(config.gateway_timeout_ms / 1000))

    if provider == "openclaw":
        return OpenClawGateway(
            gateway_url=config.openclaw_gateway_url or config.gateway_http_url,
            token=config.gateway_auth_token,
            agent_id=config.gateway_agent_id or "assistant",
            session_prefix=config.gateway_session_prefix,
            timeout_s=timeout_s,
            agent_response_timeout_s=max(timeout_s, int(config.gateway_agent_response_timeout_ms / 1000)),
        )
    if provider in {"generic", "fake", "http", "test"}:
        return GenericGateway(
            http_url=config.gateway_http_url,
            http_endpoint=config.gateway_http_endpoint,
            ws_url=config.gateway_ws_url,
            timeout_s=timeout_s,
        )
    if provider == "zeroclaw":
        return ZeroClawGateway(
            gateway_url=config.zeroclaw_gateway_url,
            webhook_token=config.zeroclaw_webhook_token,
            channel=config.zeroclaw_channel,
            timeout_s=timeout_s,
        )
    if provider == "tinyclaw":
        return TinyClawGateway(
            tinyclaw_home=config.tinyclaw_home,
            agent_id=config.tinyclaw_agent_id or "default",
            timeout_s=timeout_s,
        )
    if provider == "ironclaw":
        return IronClawGateway(
            gateway_url=config.ironclaw_gateway_url,
            token=config.ironclaw_gateway_token,
            agent_id=config.ironclaw_agent_id or "default",
            use_websocket=config.ironclaw_use_websocket,
            timeout_s=timeout_s,
        )
    if provider == "mimiclaw":
        return MimiClawGateway(
            device_host=config.mimiclaw_device_host,
            device_port=config.mimiclaw_device_port,
            use_websocket=config.mimiclaw_use_websocket,
            telegram_bot_token=config.mimiclaw_telegram_bot_token,
            telegram_chat_id=config.mimiclaw_telegram_chat_id,
            timeout_s=timeout_s,
        )
    if provider == "picoclaw":
        return PicoClawGateway(
            workspace_home=config.picoclaw_home,
            gateway_url=config.picoclaw_gateway_url,
            agent_id=config.picoclaw_agent_id or "default",
            timeout_s=timeout_s,
        )
    if provider == "nanobot":
        return NanoBotGateway(
            workspace_home=config.nanobot_home,
            gateway_url=config.nanobot_gateway_url,
            agent_id=config.nanobot_agent_id or "",
            timeout_s=timeout_s,
        )

    return OpenClawGateway(
        gateway_url=config.openclaw_gateway_url or config.gateway_http_url,
        token=config.gateway_auth_token,
        agent_id=config.gateway_agent_id or "assistant",
        session_prefix=config.gateway_session_prefix,
        timeout_s=timeout_s,
        agent_response_timeout_s=max(timeout_s, int(config.gateway_agent_response_timeout_ms / 1000)),
    )
