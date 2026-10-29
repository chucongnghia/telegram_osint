"""Project-owned paths, independent of the caller's working directory.

Explicit paths supplied on the command line remain relative to the caller.
JSON files intentionally stay in json_folder/ for backwards-compatible data lookup.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
JSON_DIR = PROJECT_ROOT / "json_folder"
SESSIONS_DIR = PROJECT_ROOT / "sessions"
MEDIA_DIR = PROJECT_ROOT / "media"
PROFILE_PHOTOS_DIR = MEDIA_DIR / "profile_photos"
