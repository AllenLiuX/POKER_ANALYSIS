"""WPK hand recorder."""

from pathlib import Path

from dotenv import load_dotenv


# Keep secrets in the recorder-local .env. Explicit process environment still wins.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

__version__ = "0.1.0"
