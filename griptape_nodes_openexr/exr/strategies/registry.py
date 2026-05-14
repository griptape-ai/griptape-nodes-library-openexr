"""JSON-driven channel grouping strategy registry.

Loads built-in strategies from settings.json shipped inside the package.
An additional strategy config file path is read from the Griptape Nodes
settings system under the key ``openexr.openexr_config`` (an absolute file
path). Configure it via Settings → OpenEXR in the UI.

Third-party studios create their own JSON file with the same schema and
set its path in that setting. Entries in external configs are merged with
and can extend (but not replace without re-registering) the built-ins.
"""

from __future__ import annotations

import importlib
import importlib.resources as _res
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from griptape_nodes_openexr.exr.strategies.base import ChannelGroupingStrategy

logger = logging.getLogger("griptape_nodes")

_registry: dict[str, ChannelGroupingStrategy] = {}
_display_names: dict[str, str] = {}


def _apply_config(data: dict, label: str) -> None:
    for entry in data.get("strategies", []):
        name = entry.get("name")
        display_name = entry.get("display_name", name)
        module_path = entry.get("module")
        class_name = entry.get("class")

        if not (name and module_path and class_name):
            logger.warning("Skipping incomplete strategy entry: %s", entry)
            continue

        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            _registry[name] = cls()
            _display_names[name] = display_name
        except (ImportError, AttributeError) as exc:
            logger.error("Failed to load strategy '%s' from '%s.%s': %s", name, module_path, class_name, exc)


def _load_builtin_config() -> None:
    try:
        text = _res.files("griptape_nodes_openexr").joinpath("settings.json").read_text(encoding="utf-8")
        data = json.loads(text)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error("Failed to load built-in channel strategy config: %s", exc)
        return
    _apply_config(data, "built-in")


def _load_config(path: Path) -> None:
    if not path.exists():
        logger.warning("Channel strategy config not found: %s", path)
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to load channel strategy config '%s': %s", path, exc)
        return
    _apply_config(data, str(path))


def _ensure_loaded() -> None:
    if _registry:
        return
    _load_builtin_config()
    try:
        from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

        extra_path = GriptapeNodes.ConfigManager().get_config_value("openexr.openexr_config", default="") or ""
    except Exception:
        extra_path = ""
    if extra_path:
        _load_config(Path(extra_path))


def get_strategy(name: str) -> ChannelGroupingStrategy:
    """Return the strategy registered under `name`.

    Raises:
        KeyError: If no strategy with that name is registered.
    """
    _ensure_loaded()
    if name not in _registry:
        available = ", ".join(f"'{n}'" for n in _registry)
        msg = f"Unknown channel grouping strategy '{name}'. Available: {available}"
        raise KeyError(msg)
    return _registry[name]


def registered_names() -> list[str]:
    """Return strategy names in registration order."""
    _ensure_loaded()
    return list(_registry.keys())


def registered_display_names() -> list[str]:
    """Return display names in the same order as registered_names()."""
    _ensure_loaded()
    return [_display_names[n] for n in _registry]
