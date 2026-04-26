from __future__ import annotations

import importlib
import pkgutil
from typing import Iterable

from .logging_setup import get_logger
from .tools.base import ToolModule

logger = get_logger(__name__)


def discover_tools() -> list[ToolModule]:
    """Import every subpackage of app.tools and collect their exported `tool` attribute."""
    import app.tools as tools_pkg

    found: list[ToolModule] = []
    for mod_info in pkgutil.iter_modules(tools_pkg.__path__):
        if not mod_info.ispkg:
            continue
        name = mod_info.name
        try:
            mod = importlib.import_module(f"app.tools.{name}")
        except Exception as e:  # noqa: BLE001
            logger.exception("Failed loading tool %s: %s", name, e)
            continue
        tool = getattr(mod, "tool", None)
        if not isinstance(tool, ToolModule):
            logger.warning("Tool package %s does not expose a ToolModule as `tool`", name)
            continue
        if not tool.metadata.enabled:
            logger.info("Tool %s is disabled", tool.metadata.id)
            continue
        found.append(tool)
        logger.info("Registered tool: %s (%s)", tool.metadata.id, tool.metadata.name)
    return found


def mount_tools(app, tools: Iterable[ToolModule]) -> None:
    for tool in tools:
        prefix = f"/tools/{tool.metadata.id}"
        app.include_router(tool.router, prefix=prefix, tags=[tool.metadata.id])
