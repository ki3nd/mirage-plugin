def parse_env_block(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip(); v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k:
            result[k] = v
    return result


def redact_secrets(text: str, secrets: dict[str, str]) -> str:
    """Replace every secret VALUE occurring in ``text`` with ``***``.

    Error messages surfaced to the LLM/user can embed resolved secret
    values (e.g. a pydantic ValidationError over a config where ``${VAR}``
    was already interpolated to plaintext, or a daemon response echoing the
    config). Scrub those values before the text leaves the plugin. Longest
    values first so a value that contains another isn't half-masked.
    """
    for v in sorted((s for s in secrets.values() if s), key=len, reverse=True):
        text = text.replace(v, "***")
    return text
