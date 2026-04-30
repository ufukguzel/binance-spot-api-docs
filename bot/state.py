from dataclasses import dataclass
from datetime import date


@dataclass
class BotState:
    has_position: bool = False
    entry_price: float = 0.0
    daily_start_equity: float = 0.0
    day: date = date.today()
