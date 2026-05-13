import functools
import json
import os
from pathlib import Path

from src.config.schema import Config


def config_root() -> Path:
    root = os.environ.get("CONFIG_ROOT")
    if root:
        return Path(root)
    return Path(__file__).parent.parent.parent


def _deep_merge(base: dict, overrides: dict) -> dict:
    result = dict(base)
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@functools.cache
def _read_defaults() -> dict:
    defaults_path = config_root() / "config" / "defaults.json"
    return json.loads(defaults_path.read_text())


def load_config(workspace_slug: str | None = None) -> Config:
    data = dict(_read_defaults())

    if workspace_slug:
        override_path = config_root() / "config" / "workspaces" / f"{workspace_slug}.json"
        if override_path.exists():
            workspace_cfg = json.loads(override_path.read_text())
            overrides = workspace_cfg.get("overrides", {})
            data = _deep_merge(data, overrides)

    return Config(**data)


def get_inbound_domain() -> str:
    domain = os.environ.get("INBOUND_DOMAIN")
    if domain:
        return domain
    return _read_defaults()["inbound_domain"]
