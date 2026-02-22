"""
占位：后续暴露 HTTP API 端点供 OpenClaw 调用。
"""


def health() -> dict:
    return {"ok": True}

