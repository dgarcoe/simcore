"""What this Simcore can do (GET /api/version), so a UI can tell a scenario needs a newer one."""

from __future__ import annotations

import os

FEATURES = [
    "lifecycle",  # health, deaths, arrivals, departures, event effects
    "items",  # inventories, stock, production, trade
    "conditions",  # conditions, scenario actions, rule-driven agents
    "world_rules",  # needs, world rules, places with state
    "minds",  # plans, beliefs, secrets, rumors; conditions that change a mind
    "conversations",  # several listeners, whisper/invite/follow, ignored speakers
    "search",  # find someone
    "story",  # chapters and key moments
    "society",  # attack/heal, groups, votes, reputation, routines
    "rpg",  # attributes, d20 checks, experience and levels
    "artifacts",  # visible / drops items, holds rules with {holder}, count.item
    "contact_contagion",  # contagion via contact
    "runs",  # every run is recorded: GET /api/runs, GET/DELETE /api/runs/{id}
    "games",  # games with players who configure their own agent (/api/games, /api/play)
    "auth",  # SIMCORE_DM_PASSWORD: only dungeon masters see the simulation
    "recipes",  # actions.where, pending actions with what they need and where it lies
    "game_systems",  # steal one/all, "*" items, scores & achievements, when.any/score, {winner},
    # cursed items, group perks (allies' bonus, common stash, mutual defense)
    "gangs",  # territories, when.territories, {mvp}, winner: group, society.lethal/knockout
    "orders",  # agents[].orders from players; pause_each_day with reports and new orders
    "review",  # llm.record_prompts (decision_trace), POST /api/narrative, private player replays
]


def version() -> dict:
    return {"ref": os.environ.get("SIMCORE_REF", ""), "features": FEATURES}
