import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)


class Settings:
    def __init__(self):
        self.ROOSTOO_API_KEY: str = os.getenv("ROOSTOO_API_KEY", "").strip()
        self.ROOSTOO_SECRET_KEY: str = os.getenv("ROOSTOO_SECRET_KEY", "").strip()
        self.ROOSTOO_BASE_URL: str = os.getenv("ROOSTOO_BASE_URL", "https://mock-api.roostoo.com").rstrip("/")
        self.TRADING_MODE: str = os.getenv("TRADING_MODE", "testing").lower()
        self.APP_ENV: str = os.getenv("APP_ENV", "production").lower()
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

        self.DATA_DIR: Path = PROJECT_ROOT / "data"
        self.LOGS_DIR: Path = PROJECT_ROOT / "logs"
        self.DB_PATH: Path = self.DATA_DIR / "trading.db"

        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.LOGS_DIR.mkdir(parents=True, exist_ok=True)

    def validate_credentials(self) -> bool:
        if not self.ROOSTOO_API_KEY or not self.ROOSTOO_SECRET_KEY:
            return False
        if "your_" in self.ROOSTOO_API_KEY or "your_" in self.ROOSTOO_SECRET_KEY:
            return False
        return True


settings = Settings()
