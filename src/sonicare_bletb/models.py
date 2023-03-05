from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=False)
class SonicareBLETBState:
    brushing_time: int = 0
    battery_level: int = 0
    routine_length: int = 0
    handle_state: str = ""
    available_brushing_routine: int = 0
    intensity: int = 0
    loaded_session_id: int = 0
    handle_time: datetime = 0
    brushing_session_id: int = 0
    last_session_id: int = 0
