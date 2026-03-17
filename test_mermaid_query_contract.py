import re
from pathlib import Path

QUERY = "Show me a mermaid chart of the population of the five most populous countries in the world."


def extract_mermaid_source(text: str) -> str:
    tagged = re.search(
        r"<(?:mermaidchart|pyramidchart)>([\s\S]*?)</(?:mermaidchart|pyramidchart)>",
        text,
        flags=re.IGNORECASE,
    )
    if tagged:
        return tagged.group(1).strip()
    return text.strip()


def meets_population_top5_requirements(query: str, response: str) -> tuple[bool, str]:
    q = query.lower()
    if "mermaid" not in q or "population" not in q or "five" not in q:
        return False, "query_intent_not_matched"

    source = extract_mermaid_source(response)
    lower = source.lower()

    has_mermaid_shape = bool(
        re.search(
            r"\b(flowchart|graph\s+(td|lr|rl|bt)|pie|xychart|bar|gantt|journey|sequenceDiagram|classDiagram|stateDiagram|erDiagram)\b",
            source,
            flags=re.IGNORECASE,
        )
    )
    if not has_mermaid_shape:
        return False, "missing_mermaid_shape"

    expected_countries = ["india", "china", "usa", "indonesia", "pakistan"]
    missing = [country for country in expected_countries if country not in lower]
    if missing:
        return False, f"missing_countries:{','.join(missing)}"

    number_hits = re.findall(r"\b\d{2,4}\b", source)
    if len(number_hits) < 5:
        return False, "missing_population_values"

    return True, "ok"


def test_query_contract_with_user_sample_response() -> None:
    # User-provided upstream sample (raw, unwrapped Mermaid-like output)
    sample_response = (
        "%% {init: {' theme': ' default'}} %%\n"
        "bar\n"
        "title Population of Top 5 Countries\n"
        "x-axis Countries\n"
        "y-axis Population (millions)\n"
        "India:1490\n"
        "China:1430\n"
        "USA:336\n"
        "Indonesia:277\n"
        "Pakistan:242"
    )

    ok, reason = meets_population_top5_requirements(QUERY, sample_response)
    assert ok, reason

def test_new_command_scrolls_chat_to_bottom() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "function requestScrollToBottomBurst()" in source
    assert "if(S.scrollToBottomPending)" in source
    assert "scrollChat();" in source
    assert "if(nextMsg && nextMsg.role==='user') requestScrollToBottomBurst();" in source

def test_scroll_down_button_visibility_and_action_contract() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert 'id="scrollDownWrap"' in source
    assert 'data-action="chat-scroll-down"' in source
    assert "function updateScrollDownButton()" in source
    assert "const shouldShow=overflow && !atBottom;" in source
    assert "const scrollDownBtn = e.target.closest('[data-action=\"chat-scroll-down\"]');" in source


def test_query_contract_with_tagged_mermaid_response() -> None:
    tagged_response = (
        "Here is the chart.\n"
        "<mermaidchart>\n"
        "bar\n"
        "title Population of Top 5 Countries (millions)\n"
        "x-axis Countries\n"
        "y-axis Population\n"
        "India:1490\n"
        "China:1430\n"
        "USA:336\n"
        "Indonesia:277\n"
        "Pakistan:242\n"
        "</mermaidchart>"
    )

    ok, reason = meets_population_top5_requirements(QUERY, tagged_response)
    assert ok, reason


if __name__ == "__main__":
    test_query_contract_with_user_sample_response()
    test_query_contract_with_tagged_mermaid_response()
    print("mermaid query contract: ok")
