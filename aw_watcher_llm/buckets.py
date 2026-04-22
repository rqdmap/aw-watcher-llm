import socket

RAW_BUCKET_TYPE = "com.rqdmap.llm.raw.v1"
DISPLAY_BUCKET_TYPE = "com.rqdmap.llm.display.v1"

RAW_SOURCES = ("opencode", "claudecode", "codex")


def default_host() -> str:
    return socket.gethostname()


def raw_bucket_id(source: str, host: str) -> str:
    normalized = source.strip().lower()
    if normalized not in RAW_SOURCES:
        raise ValueError(f"unsupported source: {source}")
    return f"aw-watcher-llm-{normalized}_{host}"


def focus_bucket_id(host: str) -> str:
    return f"aw-watcher-llm-focus_{host}"
