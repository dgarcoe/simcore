"""Bots: made-up players the DM adds to a game to try it out. Each gets an agent that respects the
game's template (only the fields players may edit, within its limits), spread across the groups
players may join, with orders of a random style. They can't log in: nobody holds their link."""

from __future__ import annotations

import random

from simcore.engine.rpg import ATTRIBUTES, SKILLS

NAMES = (
    "Alba", "Bruno", "Carla", "Dario", "Elena", "Fede", "Gala", "Hugo", "Irene", "Joel", "Kira",
    "Leo", "Maya", "Nico", "Olga", "Pau", "Quique", "Rosa", "Saúl", "Tina", "Ulises", "Vera",
    "Wendy", "Xavi", "Yago", "Zoe", "Abril", "Bea", "Ciro", "Dina", "Enzo", "Flor", "Gael",
    "Hana", "Iker", "Jara", "Kai", "Lía", "Marco", "Nora", "Óscar", "Paula", "Raúl", "Sara",
    "Teo", "Úrsula", "Vito", "Yara", "Adri", "Blanca", "César", "Dafne", "Eloy", "Fátima",
    "Gus", "Helena", "Ismael", "Julia", "Lucas", "Mara",
)
PROFESSIONS = ("comerciante", "mecánica", "cocinero", "estudiante", "vigilante", "enfermera",
               "conductor", "artista")
STYLES = ("agresivo", "negociador", "sigiloso", "leal", "oportunista", "empresario")
RISKS = ("careful", "bold", "reckless")


def bot_agent(game: dict, scenario: dict | None, taken: set[str], rng: random.Random) -> dict:
    """An agent with only what the template lets players edit, filled at random."""
    t = game["template"]
    can = set(t.get("editable") or [])
    name = next((n for n in rng.sample(NAMES, len(NAMES)) if n.lower() not in taken), "")
    if not name:
        n = 1
        while f"bot {n}" in taken:
            n += 1
        name = f"Bot {n}"
    agent: dict = {"name": name}
    groups = list(t.get("factions") or []) or list(((scenario or {}).get("factions") or {}))
    if "faction" in can and groups:
        # the least crowded group first, so teams stay even
        count = {g: 0 for g in groups}
        for p in game["players"]:
            g = (p.get("agent") or {}).get("faction")
            if g in count:
                count[g] += 1
        least = min(count.values())
        agent["faction"] = rng.choice([g for g, c in count.items() if c == least])
    if "profession" in can:
        agent["profession"] = rng.choice(list(t.get("professions") or PROFESSIONS))
    if "age" in can:
        agent["age"] = rng.randint(18, 70)
    if "personality" in can:
        agent["personality"] = {k: round(rng.uniform(0.15, 0.85), 2) for k in
                                ("openness", "conscientiousness", "extraversion",
                                 "agreeableness", "neuroticism")}
    if "goals" in can:
        goal = ((scenario or {}).get("factions") or {}).get(agent.get("faction") or "", {})
        agent["goals"] = [str((goal or {}).get("goal") or "Salir adelante")][: int(t["max_goals"])]
    if "backstory" in can:
        agent["backstory"] = f"{name} es un bot que el DM ha añadido para probar la partida."
    if "attributes" in can:
        attrs = {a: 10 for a in ATTRIBUTES}
        budget = int(t["attribute_budget"])
        for _ in range(budget):
            a = rng.choice([a for a in ATTRIBUTES if attrs[a] < int(t["attribute_max"])] or
                           list(ATTRIBUTES))
            attrs[a] = min(int(t["attribute_max"]), attrs[a] + 1)
        agent["attributes"] = attrs
    if "skills" in can:
        agent["skills"] = rng.sample(sorted(SKILLS), min(int(t["max_skills"]), len(SKILLS)))
    if "orders" in can:
        priorities = ["tener más puntos que nadie", "sobrevivir"]
        if groups:
            priorities.insert(rng.randint(0, 1), "que gane mi grupo")
        agent["orders"] = {
            "style": rng.choice(STYLES),
            "risk": rng.choice(RISKS),
            "priorities": priorities,
            **({"red_lines": [{"action": "attack", "target": "my_group"}]}
               if groups and rng.random() < 0.6 else {}),
        }
    return agent
