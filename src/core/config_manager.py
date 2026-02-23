from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
_CACHED_CONFIG: Optional[dict[str, Any]] = None

# 自动加载根目录的 .env 文件内容到环境变量
load_dotenv(BASE_DIR / ".env")


def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """读取环境变量的简单封装。支持自动从基于根目录的 .env 加载。"""
    return os.environ.get(key, default)


def get_project_path(*parts: str) -> Path:
    """基于项目根目录拼接路径。"""
    return BASE_DIR.joinpath(*parts)


def load_yaml_config(path: Optional[Path] = None) -> dict[str, Any]:
    """加载 YAML 配置（默认读取项目根目录下的 config.yaml）。"""
    global _CACHED_CONFIG
    if _CACHED_CONFIG is not None and path is None:
        return _CACHED_CONFIG

    cfg_path = path or get_project_path("config.yaml")
    if not cfg_path.exists():
        data: dict[str, Any] = {}
    else:
        text = cfg_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}

    if path is None:
        _CACHED_CONFIG = data
    return data

