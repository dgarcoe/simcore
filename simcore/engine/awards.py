"""End-of-run awards and each agent's highlights, counted from the run's events as they happen.

Awards are titles for whoever did most of something (most knockouts dealt, most steals, the
luckiest dice, the one who changed sides most…). Highlights are the three moments of each agent
that matter most. Both go into `simulation_end` (`awards`, `highlights`) as plain data: the UI
names and words them.
"""

from __future__ import annotations

from collections import defaultdict

from simcore.engine.events import EventType, SimEvent

# key → what is counted; the agent with the highest count wins (ties: most points, then name)
AWARDS = (
    "knockouts",  # knockouts and kills dealt
    "brawler",  # attacks that hit
    "punching_bag",  # times knocked out
    "thief",  # steals
    "talker",  # times they spoke
    "wanderer",  # different places visited
    "lucky",  # critical rolls
    "unlucky",  # fumbles
    "turncoat",  # groups left or joined
    "tempted",  # times a red line stopped them
    "schemer",  # their player's plans that fired
    "pacifist",  # no attack at all, most points among those
    "untouchable",  # never hit, most points among those
)
HIGHLIGHTS = 3


class AwardsTracker:
    def __init__(self) -> None:
        self.count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.places: dict[str, set[str]] = defaultdict(set)
        self.attacked: set[str] = set()  # attacked someone
        self.hit: set[str] = set()  # was hit
        self.moments: dict[str, list[dict]] = defaultdict(list)

    def _moment(self, agent: str, tick: int, importance: float, kind: str, **data) -> None:
        if not agent:
            return
        self.moments[agent].append({"tick": tick, "importance": importance, "kind": kind, **data})

    def handle(self, event: SimEvent) -> None:
        d = event.data or {}
        t = event.tick
        who = str(d.get("agent") or event.source or "")
        kind = event.type
        if kind in (EventType.AGENT_ACTION, EventType.AGENT_MOVE, EventType.AGENT_SPEAK):
            actor = event.source
            if d.get("location"):
                self.places[actor].add(str(d["location"]))
            action_type = d.get("action_type")
            target = str(d.get("target") or "")
            if action_type in ("speak", "whisper"):
                self.count["talker"][actor] += 1
            if action_type == "attack":
                self.attacked.add(actor)
                if d.get("hit"):
                    self.count["brawler"][actor] += 1
                    self.hit.add(target)
                if d.get("killed") or d.get("knocked_out"):
                    self.count["knockouts"][actor] += 1
                    how = "killed" if d.get("killed") else "knocked_out"
                    self._moment(actor, t, 0.9 if how == "killed" else 0.7, how, target=target,
                                 weapon=str(d.get("weapon") or ""))
            elif action_type == "custom" and d.get("name") and d.get("success", True):
                self._moment(actor, t, 0.5 if target else 0.35, "custom", name=str(d["name"]),
                             text=str(d.get("action") or ""), target=target)
        elif kind == EventType.KNOCKOUT:
            self.count["punching_bag"][who] += 1
            self.hit.add(who)
            self._moment(who, t, 0.6, "was_knocked_out", cause=str(d.get("cause") or ""),
                         place=str(d.get("location") or ""))
        elif kind == EventType.AGENT_DEATH:
            self._moment(who, t, 1.0, "died", cause=str(d.get("cause") or ""))
        elif kind == EventType.DICE_ROLL:
            if d.get("critical"):
                self.count["lucky"][who] += 1
                self._moment(who, t, 0.45, "critical", check=str(d.get("check") or ""),
                             target=str(d.get("target") or ""))
            if d.get("fumble"):
                self.count["unlucky"][who] += 1
                self._moment(who, t, 0.4, "fumble", check=str(d.get("check") or ""),
                             target=str(d.get("target") or ""))
        elif kind == EventType.ITEM_TRANSFER:
            if d.get("kind") == "steal" and d.get("to"):
                self.count["thief"][str(d["to"])] += 1
                self._moment(str(d["to"]), t, 0.55, "stole", item=str(d.get("item") or ""),
                             amount=d.get("amount") or 1, target=str(d.get("from") or ""))
            elif d.get("kind") == "holder" and d.get("to"):
                self._moment(str(d["to"]), t, 0.75, "holder", item=str(d.get("item") or ""))
        elif kind == EventType.FACTION_CHANGE:
            change = str(d.get("change") or "")
            if change in ("left", "joined"):
                self.count["turncoat"][who] += 1
            if change in ("left", "joined", "founded", "expelled", "leader"):
                self._moment(who, t, 0.6, "group", change=change, group=str(d.get("faction") or ""))
        elif kind == EventType.ORDERS:
            if d.get("kind") == "red_line":
                self.count["tempted"][who] += 1
            else:
                self.count["schemer"][who] += 1
                self._moment(who, t, 0.4, "plan", text=str(d.get("text") or ""))
        elif kind == EventType.SCORE_CHANGE:
            points = int(d.get("points") or 0)
            if abs(points) >= 5:
                self._moment(who, t, 0.55 if points > 0 else 0.5, "points", points=points,
                             description=str(d.get("description") or d.get("achievement") or ""))
        elif kind == EventType.CONDITION_CHANGE:
            if d.get("change") == "transformed":
                self._moment(who, t, 0.9, "transformed", into=str(d.get("into") or ""))

    def results(self, everyone: list[str], points: dict[str, int]) -> tuple[list[dict], dict]:
        """Awards [{key, agent, value}] and each agent's highlights (most important first)."""
        for name, places in self.places.items():
            self.count["wanderer"][name] = len(places)

        def best(candidates: dict[str, int]) -> tuple[str, int] | None:
            items = [(n, v) for n, v in candidates.items() if v > 0 and n in everyone]
            if not items:
                return None
            return min(items, key=lambda x: (-x[1], -points.get(x[0], 0), x[0]))

        awards = []
        for key in AWARDS:
            if key == "pacifist":
                pool = {n: points.get(n, 0) + 1 for n in everyone if n not in self.attacked}
                found = best(pool) if len(pool) < len(everyone) else None
                value = 0
            elif key == "untouchable":
                pool = {n: points.get(n, 0) + 1 for n in everyone if n not in self.hit}
                found = best(pool) if len(pool) < len(everyone) else None
                value = 0
            else:
                found = best(self.count[key])
                value = found[1] if found else 0
            if found:
                awards.append({"key": key, "agent": found[0], "value": value})
        highlights = {}
        for name in everyone:
            top = sorted(self.moments.get(name, []), key=lambda m: (-m["importance"], m["tick"]))
            if top:
                highlights[name] = sorted(top[:HIGHLIGHTS], key=lambda m: m["tick"])
        return awards, highlights
