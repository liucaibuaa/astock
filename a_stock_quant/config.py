"""Configuration management: load config.yaml and merge with defaults."""


import os
from copy import deepcopy
from pathlib import Path

import yaml

DEFAULT_CONFIG: dict = {
    "watchlist": [],
    "poll_interval_seconds": 5,
    "trading_hours_only": True,
    "alerts": {
        "price_change_pct": {"enabled": True, "threshold": 3.0, "cooldown_seconds": 60},
        "volume_spike": {"enabled": False, "multiplier": 3.0, "cooldown_seconds": 300},
        "turnover_pct": {"enabled": True, "threshold": 5.0, "cooldown_seconds": 60},
        "limit_up_down_proximity": {"enabled": True, "within_pct": 1.0, "cooldown_seconds": 120},
        "indicator_signals": {
            "enabled": False,
            "rules": [],
            "cooldown_seconds": 300,
        },
    },
    "database": {
        "path": "data/snapshots.db",
        "retention_days": 30,
    },
    "web": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 8765,
    },
    "notifications": {
        "console": {"enabled": True},
        "sound": {"enabled": False, "sound_file": None},
        "webhook": {
            "enabled": False,
            "dingtalk_url": None,
            "wechat_work_url": None,
        },
    },
}

_config_cache: dict | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _find_config_path() -> Path:
    """Locate config.yaml: env var > cwd > project root > XDG."""
    env_path = os.getenv("ASTOCK_QUANT_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    cwd_path = Path.cwd() / "config.yaml"
    if cwd_path.exists():
        return cwd_path

    pkg_root = Path(__file__).resolve().parent.parent
    pkg_path = pkg_root / "config.yaml"
    if pkg_path.exists():
        return pkg_path

    xdg_path = Path.home() / ".config" / "a_stock_quant" / "config.yaml"
    if xdg_path.exists():
        return xdg_path

    return pkg_path  # default, even if missing


def load_config(config_path: Path | str | None = None) -> dict:
    """Load configuration from a YAML file, merging with defaults.

    Resolves relative database paths against the project root.
    """
    global _config_cache

    if config_path is None:
        config_path = _find_config_path()
    else:
        config_path = Path(config_path)

    config = deepcopy(DEFAULT_CONFIG)

    if config_path.exists():
        with open(config_path, encoding="utf-8") as fh:
            user_config = yaml.safe_load(fh) or {}
        config = _deep_merge(config, user_config)

    # Resolve relative database path
    db_path = config["database"]["path"]
    if not Path(db_path).is_absolute():
        project_root = config_path.parent.resolve()
        config["database"]["path"] = str(project_root / db_path)

    _config_cache = config
    return config


def get_config() -> dict:
    """Return the cached configuration. Call load_config() first."""
    global _config_cache
    if _config_cache is None:
        return load_config()
    return _config_cache


def reload_config(config_path: Path | str | None = None) -> dict:
    """Force re-read configuration from disk."""
    return load_config(config_path)
