import json
from pathlib import Path

WORKFLOWS_DIR = Path.home() / ".cmdw"
WORKFLOWS_PATH = WORKFLOWS_DIR / "workflows.json"


def _ensure_dir():
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)


def load_workflows():
    _ensure_dir()
    if not WORKFLOWS_PATH.exists():
        return {}
    try:
        with WORKFLOWS_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_workflows(data):
    _ensure_dir()
    with WORKFLOWS_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
