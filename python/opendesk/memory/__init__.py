"""opendesk.memory — screen memory: a local, searchable desktop history.

A low-frequency background loop captures the screen, runs OCR locally, and
stores the text plus a small thumbnail in ``~/.opendesk/memory``.  The
``memory`` tool lets an agent search that history ("what error did I see
in the terminal on Tuesday?").  Nothing leaves the machine.

Public surface::

    from opendesk.memory import MemoryStore, MemoryConfig, ScreenMemoryRecorder
"""

from opendesk.memory.config import (
    MemoryConfig,
    clear_pause,
    daemon_alive,
    get_pause,
    is_paused,
    load_config,
    memory_dir,
    save_config,
    set_pause,
)
from opendesk.memory.recorder import ScreenMemoryRecorder, start_daemon
from opendesk.memory.store import Frame, MemoryStore, StoreStats
from opendesk.memory.timeparse import parse_range, parse_when

__all__ = [
    "Frame",
    "MemoryConfig",
    "MemoryStore",
    "ScreenMemoryRecorder",
    "StoreStats",
    "clear_pause",
    "daemon_alive",
    "get_pause",
    "is_paused",
    "load_config",
    "memory_dir",
    "parse_range",
    "parse_when",
    "save_config",
    "set_pause",
    "start_daemon",
]
