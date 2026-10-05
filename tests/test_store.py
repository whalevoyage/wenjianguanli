import json
from pathlib import Path
from backend import store


def test_save_json_is_atomic_and_round_trips(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "data.json"
    store.save_json(target, {"ok": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert not list(target.parent.glob("*.tmp"))
