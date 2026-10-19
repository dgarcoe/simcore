"""Editing built-in scenarios in place (kept as an override) and restoring them."""

import yaml

import simcore.server.scenarios as scenarios
from simcore.server.scenarios import ScenarioStore

ORIGINAL = {
    "simulation": {"name": "Village", "duration": "6 hours"},
    "environment": {"locations": [{"name": "Plaza"}]},
    "agents": [{"name": "Ana", "starting_location": "Plaza"}],
}


def make_store(tmp_path, monkeypatch) -> ScenarioStore:
    builtin = tmp_path / "builtin"
    (builtin / "village").mkdir(parents=True)
    (builtin / "village" / "config.yaml").write_text(yaml.safe_dump(ORIGINAL), encoding="utf-8")
    monkeypatch.setattr(scenarios, "_BUILTIN_DIR", builtin)
    return ScenarioStore(tmp_path / "user")


def test_saving_a_builtin_modifies_it_instead_of_copying(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    edited = {**ORIGINAL, "simulation": {"name": "Village at night", "duration": "8 hours"}}
    rec = store.update("builtin:village", edited)
    assert rec["id"] == "builtin:village" and rec["builtin"] and rec["modified"]
    listed = store.list_all()
    assert [s["id"] for s in listed] == ["builtin:village"]  # no copy
    assert listed[0]["meta"]["name"] == "Village at night"
    assert store.load_engine_config("builtin:village").duration == "8 hours"
    # the YAML itself is untouched
    assert (
        "Village at night" not in (scenarios._BUILTIN_DIR / "village" / "config.yaml").read_text()
    )


def test_restore_drops_the_edits(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    store.update(
        "builtin:village", {**ORIGINAL, "simulation": {"name": "Other", "duration": "1 hour"}}
    )
    assert store.delete("builtin:village") is True
    rec = store.get("builtin:village")
    assert rec["meta"]["name"] == "Village" and not rec.get("modified")
    assert store.delete("builtin:village") is False
    assert store.update("builtin:nope", ORIGINAL) is None


def test_user_scenarios_update_in_place(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    rec = store.create(ORIGINAL)
    store.update(rec["id"], {**ORIGINAL, "simulation": {"name": "Renamed", "duration": "6 hours"}})
    users = [s for s in store.list_all() if not s["builtin"]]
    assert len(users) == 1 and users[0]["meta"]["name"] == "Renamed"
