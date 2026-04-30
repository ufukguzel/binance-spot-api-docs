from datetime import date

from state import BotState


def reset_daily_state_if_needed(state: BotState) -> None:
    today = date.today()
    if state.day != today:
        state.day = today
        state.daily_start_equity = 0.0


def can_trade(equity: float, state: BotState, max_daily_loss_pct: float) -> bool:
    reset_daily_state_if_needed(state)

    if state.daily_start_equity <= 0:
        state.daily_start_equity = equity
        return True

    threshold = state.daily_start_equity * (1 - (max_daily_loss_pct / 100))
    return equity > threshold
