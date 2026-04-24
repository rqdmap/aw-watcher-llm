import socket

RAW_BUCKET_TYPE = "com.rqdmap.llm.raw.v1"
SESSION_BUCKET_TYPE = "com.rqdmap.llm.session.workspace.v1"
SESSION_WORKSPACE_HOST_PREFIX = "llm-workspace-"

RAW_SOURCES = ("opencode", "claudecode", "codex", "qoder")


def default_host() -> str:
    return socket.gethostname()


def raw_bucket_id(source: str, host: str) -> str:
    normalized = _normalize_source(source)
    return f"aw-watcher-llm-{normalized}_{host}"


def session_bucket_prefix(source: str, host: str) -> str:
    normalized = _normalize_source(source)
    return f"aw-watcher-llm-session-{normalized}_{host}_"


def session_bucket_id(source: str, host: str, session_id: str) -> str:
    return f"{session_bucket_prefix(source, host)}{session_id}"


def session_workspace_host(host: str) -> str:
    normalized = host.strip()
    if not normalized:
        raise ValueError("host must not be empty")
    if normalized.startswith(SESSION_WORKSPACE_HOST_PREFIX):
        return normalized
    return f"{SESSION_WORKSPACE_HOST_PREFIX}{normalized}"


def _normalize_source(source: str) -> str:
    normalized = source.strip().lower()
    if normalized not in RAW_SOURCES:
        raise ValueError(f"unsupported source: {source}")
    return normalized
