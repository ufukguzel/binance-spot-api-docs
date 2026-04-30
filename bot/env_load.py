from pathlib import Path

from dotenv import load_dotenv


def load_bot_env() -> None:
    """bot/.env yolunu sabitler; `python bot/...` proje kökünden çalışsa da yüklenir."""
    load_dotenv(Path(__file__).resolve().parent / ".env")
