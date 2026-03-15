def estimate_spoken_prefix(text: str, elapsed_s: float, total_s: float) -> str:
    if total_s <= 0 or elapsed_s <= 0:
        return ""
    words = text.split()
    if not words:
        return ""
    fraction = min(1.0, elapsed_s / total_s)
    spoken_count = int(len(words) * fraction)
    if spoken_count <= 0:
        return ""
    return " ".join(words[:spoken_count])


def strip_spoken_prefix(new_text: str, previous_text: str, elapsed_s: float, total_s: float) -> str:
    prefix = estimate_spoken_prefix(previous_text, elapsed_s, total_s)
    if not prefix:
        return new_text
    prefix_words = prefix.split()
    new_words = new_text.split()
    if len(new_words) >= len(prefix_words):
        if [w.lower() for w in new_words[:len(prefix_words)]] == [w.lower() for w in prefix_words]:
            return " ".join(new_words[len(prefix_words):]).strip()
    return new_text
