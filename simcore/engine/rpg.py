"""Role-playing rules (simulation.rpg): attributes, skills, d20 checks, experience and levels.
Attacks, healing, scenario actions, persuading / deceiving / intimidating and spotting tracks
are decided by dice. Mixed into SimulationEngine; without rpg nothing changes."""

from __future__ import annotations

import unicodedata
from collections import Counter
from typing import TYPE_CHECKING

from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent
    from simcore.config.schema import AgentConfig

ATTRIBUTES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
SHORT = {
    "strength": "STR",
    "dexterity": "DEX",
    "constitution": "CON",
    "intelligence": "INT",
    "wisdom": "WIS",
    "charisma": "CHA",
}
SKILLS = {
    "athletics": "strength",
    "intimidation": "strength",
    "stealth": "dexterity",
    "sleight_of_hand": "dexterity",
    "medicine": "wisdom",
    "perception": "wisdom",
    "insight": "wisdom",
    "investigation": "intelligence",
    "persuasion": "charisma",
    "deception": "charisma",
}
INTENTS = {
    "persuade": ("charisma", "persuasion"),
    "deceive": ("charisma", "deception"),
    "intimidate": ("charisma", "intimidation"),
}
LEVELS = [0, 100, 250, 450, 700, 1000, 1400, 1900, 2500, 3200, 4000]
# (profession fragment, attribute bonuses, skills)
PROFESSIONS = [
    (
        ("guard", "guardia", "soldad", "soldier", "cazador", "hunter", "sargent", "cabo", "polic"),
        {"strength": 3, "dexterity": 2, "constitution": 2},
        ["athletics", "intimidation", "perception"],
    ),
    (
        ("médic", "medic", "doctor", "enfermer", "nurse", "sanitari", "veterinari"),
        {"wisdom": 3, "intelligence": 2},
        ["medicine", "insight"],
    ),
    (
        ("sacerdot", "priest", "padre", "monj"),
        {"wisdom": 3, "charisma": 2},
        ["insight", "persuasion", "medicine"],
    ),
    (
        (
            "estudiant",
            "student",
            "científic",
            "scientist",
            "ingenier",
            "engineer",
            "electricist",
            "radioaficion",
        ),
        {"intelligence": 3, "dexterity": 1},
        ["investigation", "sleight_of_hand"],
    ),
    (
        (
            "tender",
            "comerci",
            "merchant",
            "taberner",
            "dueñ",
            "alcald",
            "mayor",
            "inspector",
            "comandant",
        ),
        {"charisma": 3, "intelligence": 1},
        ["persuasion", "deception", "insight"],
    ),
    (
        ("granjer", "farmer", "pastor", "jornaler", "minero", "miner", "obrer"),
        {"strength": 2, "constitution": 3},
        ["athletics"],
    ),
    (
        ("ladr", "thief", "pícaro", "rogue", "contrabandist"),
        {"dexterity": 3, "charisma": 1},
        ["stealth", "sleight_of_hand", "deception"],
    ),
]


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def modifier(value: int) -> int:
    return (value - 10) // 2


def level_for(xp: int) -> int:
    level = 1
    for i, need in enumerate(LEVELS):
        if xp >= need:
            level = i + 1
    return level


def proficiency(level: int) -> int:
    return 2 + (level - 1) // 4


def derive_sheet(personality, profession: str) -> tuple[dict[str, int], list[str]]:
    """Attributes and skills from personality (OCEAN 0..1) and profession, deterministic."""
    p = personality
    attrs = {
        "strength": 10 - round((p.agreeableness - 0.5) * 2),
        "dexterity": 10 + round((p.extraversion - 0.5) * 2),
        "constitution": 10 + round((0.5 - p.neuroticism) * 4),
        "intelligence": 10 + round((p.openness - 0.5) * 6),
        "wisdom": 10 + round((p.conscientiousness - 0.5) * 4) - round((p.neuroticism - 0.5) * 2),
        "charisma": 10 + round((p.extraversion - 0.5) * 6) + round((p.agreeableness - 0.5) * 2),
    }
    skills: list[str] = []
    prof = _plain(profession)
    for fragments, bonus, extra in PROFESSIONS:
        if any(_plain(f) in prof for f in fragments):
            for k, v in bonus.items():
                attrs[k] += v
            skills += [s for s in extra if s not in skills]
            break
    return {k: max(3, min(18, v)) for k, v in attrs.items()}, skills


class RpgMixin:
    """Needs the engine's config, agents, event_bus, clock and _rng."""

    def _init_rpg(self) -> None:
        self._sheets: dict[str, dict] = {}
        self._caught_lying: dict[tuple[str, str], int] = {}

    @property
    def rpg(self) -> bool:
        return bool(getattr(self.config, "rpg", False))

    def _init_sheet(self, agent: Agent, cfg: AgentConfig) -> None:
        attrs, skills = derive_sheet(agent.persona.personality, agent.persona.profession)
        attrs.update({k: int(v) for k, v in (cfg.attributes or {}).items()})
        if cfg.skills:
            skills = list(dict.fromkeys(cfg.skills))
        level = max(1, cfg.level)
        self._sheets[agent.name] = {
            "attributes": attrs,
            "skills": set(skills),
            "level": level,
            "xp": LEVELS[min(level, len(LEVELS)) - 1],
            "uses": Counter(),
            "skill_uses": Counter(),
        }

    def _sheet(self, agent: Agent) -> dict:
        if agent.name not in self._sheets:
            from simcore.config.schema import AgentConfig

            self._init_sheet(agent, AgentConfig(name=agent.name))
        return self._sheets[agent.name]

    def _bonus(self, agent: Agent, attribute: str, skill: str = "") -> int:
        sheet = self._sheet(agent)
        bonus = modifier(sheet["attributes"].get(attribute, 10))
        if skill and skill in sheet["skills"]:
            bonus += proficiency(sheet["level"])
        return bonus

    # ── Checks ─────────────────────────────────────────────────────────

    async def _check(
        self,
        agent: Agent,
        attribute: str,
        skill: str = "",
        *,
        dc: int | None = None,
        against: tuple[Agent, str, str] | None = None,
        label: str = "",
        target: str = "",
        extra: int = 0,
        tick: int = 0,
    ) -> dict:
        """d20 + modifiers against a difficulty or another agent's roll. Emits dice_roll."""
        sheet = self._sheet(agent)
        sheet["uses"][attribute] += 1
        if skill:
            sheet["skill_uses"][skill] += 1
        natural = self._rng.randint(1, 20)
        mod = self._bonus(agent, attribute, skill) + extra
        total = natural + mod
        result = {
            "agent": agent.name,
            "check": label,
            "attribute": attribute,
            "skill": skill,
            "roll": natural,
            "modifier": mod,
            "total": total,
            "target": target,
            "critical": natural == 20,
            "fumble": natural == 1,
        }
        if against is not None:
            other, o_attr, o_skill = against
            o_nat = self._rng.randint(1, 20)
            o_total = o_nat + self._bonus(other, o_attr, o_skill)
            result["against"] = {
                "agent": other.name,
                "attribute": o_attr,
                "skill": o_skill,
                "roll": o_nat,
                "total": o_total,
            }
            success = total > o_total
        else:
            result["dc"] = dc if dc is not None else 10
            success = total >= result["dc"]
        if result["critical"]:
            success = True
        if result["fumble"]:
            success = False
        result["success"] = success
        await self.event_bus.emit(
            SimEvent(type=EventType.DICE_ROLL, tick=tick, data=result, source=agent.name)
        )
        if success:
            gain = 10 + max(
                0, (result.get("dc") or (result.get("against") or {}).get("total", 10)) - 10
            )
            await self._gain_xp(agent, gain + (10 if result["critical"] else 0), tick)
        return result

    def _chance(self, agent: Agent, attribute: str, skill: str, dc: int) -> float:
        need = dc - self._bonus(agent, attribute, skill)
        return max(0.05, min(0.95, (21 - need) / 20))

    def _defense(self, agent: Agent) -> int:
        armor = max(
            (
                spec.armor
                for item, q in agent.state.resources.items()
                if q > 0 and (spec := self.config.items.get(item)) and spec.armor
            ),
            default=0,
        )
        return 10 + modifier(self._sheet(agent)["attributes"]["dexterity"]) + armor

    async def _attack_roll(
        self, agent: Agent, victim: Agent, item: str, chance: float, tick: int
    ) -> tuple[bool, bool, bool]:
        """(hit, critical, fumble); without rpg the weapon's chance decides."""
        if not self.rpg:
            return self._rng.random() < chance, False, False
        spec = self.config.items.get(item) if item else None
        ranged = bool(spec and spec.weapon and spec.weapon.ranged)
        attribute = "dexterity" if ranged else "strength"
        res = await self._check(
            agent,
            attribute,
            "athletics" if not ranged else "",
            dc=self._defense(victim),
            label="attack",
            target=victim.name,
            tick=tick,
        )
        return res["success"], res["critical"], res["fumble"]

    async def _heal_roll(self, agent: Agent, with_item: bool, patient: Agent, tick: int) -> float:
        """How much of the healing lands: 1.5 critical, 1 success, 0.5 failure, 0 fumble."""
        if not self.rpg:
            return 1.0
        res = await self._check(
            agent,
            "wisdom",
            "medicine",
            dc=10 if with_item else 12,
            label="heal",
            target=patient.name if patient is not agent else "",
            tick=tick,
        )
        if res["fumble"]:
            return 0.0
        if res["critical"]:
            return 1.5
        return 1.0 if res["success"] else 0.5

    async def _action_roll(
        self, agent: Agent, target: Agent | None, act, name: str, tick: int
    ) -> bool:
        """A scenario action's check (rpg + check) or its plain chance."""
        check = getattr(act, "check", None)
        if not self.rpg or check is None:
            return self._rng.random() < act.chance
        against = None
        if check.against and target is not None:
            against = (target, check.against, check.against_skill)
        res = await self._check(
            agent,
            check.attribute,
            check.skill,
            dc=check.dc if against is None else None,
            against=against,
            label=name,
            target=target.name if target else "",
            tick=tick,
        )
        return res["success"]

    async def _speech_roll(self, agent: Agent, action, listeners: list[Agent], tick: int) -> None:
        """persuade / deceive / intimidate someone: an opposed check against their insight."""
        intent = (action.intent or "").strip().lower()
        if not self.rpg or intent not in INTENTS or not listeners:
            return
        attribute, skill = INTENTS[intent]
        if (
            intent == "intimidate"
            and self._sheet(agent)["attributes"]["strength"]
            > self._sheet(agent)["attributes"]["charisma"]
        ):
            attribute = "strength"
        for other in listeners:
            res = await self._check(
                agent,
                attribute,
                skill,
                against=(other, "wisdom", "insight"),
                label=intent,
                target=other.name,
                tick=tick,
            )
            ok = res["success"]
            if intent == "persuade":
                other.observe(
                    f"{agent.name} {'convinced me' if ok else 'tried to convince me, without success'}: "
                    f'"{action.content}"',
                    tick,
                    importance=0.7 if ok else 0.4,
                )
                other.memory.update_relationship(
                    agent.name, 0.1 if ok else 0.0, tick, "persuaded me" if ok else ""
                )
            elif intent == "intimidate":
                other.observe(
                    f"{agent.name} {'intimidated me' if ok else 'tried to intimidate me; I am not afraid'}: "
                    f'"{action.content}"',
                    tick,
                    importance=0.8 if ok else 0.5,
                )
                other.memory.update_relationship(
                    agent.name, -0.2 if ok else -0.1, tick, "threatened me"
                )
                if ok:
                    self._feel(other, "afraid", agent.name, tick)
            elif not ok:  # deceive, caught
                self._caught_lying[(agent.name, other.name)] = tick
                other.observe(f"I could tell {agent.name} was lying to me.", tick, importance=0.8)
                other.memory.update_relationship(agent.name, -0.3, tick, "lied to me")
                if any(w is not other for w in self._witnesses(agent)):
                    self._rep(agent.name, -0.1)

    def _caught(self, speaker: str, listener: str, tick: int) -> bool:
        return self._caught_lying.get((speaker, listener)) == tick

    async def _track_roll(self, agent: Agent, target: Agent, tick: int) -> bool:
        """Spotting a clue about where someone went (perception, DC 12)."""
        if not self.rpg:
            return False
        res = await self._check(
            agent, "wisdom", "perception", dc=12, label="track", target=target.name, tick=tick
        )
        return res["success"]

    # ── Experience ─────────────────────────────────────────────────────

    async def _gain_xp(self, agent: Agent, amount: int, tick: int) -> None:
        if not self.rpg or amount <= 0 or not agent.alive:
            return
        sheet = self._sheet(agent)
        sheet["xp"] += amount
        while level_for(sheet["xp"]) > sheet["level"]:
            sheet["level"] += 1
            improved = {}
            uses = sheet["uses"]
            attr = max(ATTRIBUTES, key=lambda a: (uses[a], sheet["attributes"][a]))
            if sheet["attributes"][attr] < 20:
                sheet["attributes"][attr] += 1
                improved["attribute"] = attr
            if sheet["level"] in (3, 5):
                unknown = [
                    (n, s) for s, n in sheet["skill_uses"].items() if s not in sheet["skills"]
                ]
                if unknown:
                    skill = max(unknown)[1]
                    sheet["skills"].add(skill)
                    improved["skill"] = skill
            agent.state.health = min(1.0, agent.state.health + 0.1)
            agent.observe(
                f"I feel more capable (level {sheet['level']}"
                + (f", better {improved['attribute']}" if improved.get("attribute") else "")
                + (f", I learned {improved['skill']}" if improved.get("skill") else "")
                + ").",
                tick,
                importance=0.6,
            )
            await self.event_bus.emit(
                SimEvent(
                    type=EventType.LEVEL_UP,
                    tick=tick,
                    data={
                        "agent": agent.name,
                        "level": sheet["level"],
                        "improved": improved,
                        "xp": sheet["xp"],
                    },
                    source=agent.name,
                )
            )

    async def _rpg_tick(self, tick: int) -> None:
        if not self.rpg:
            return
        minutes = tick * self.clock.tick_duration_minutes
        if (minutes % 1440) // 60 == 6 and minutes % 60 == 0 and tick > 1:  # survived the night
            for agent in self.living_agents:
                found = self._mind(agent) if hasattr(self, "_mind") else None
                if self._controller(agent) is None and not found:
                    await self._gain_xp(agent, 10, tick)

    # ── Prompt and state ───────────────────────────────────────────────

    def _rpg_prompt(self, agent: Agent) -> str:
        if not self.rpg:
            return ""
        s = self._sheet(agent)
        a = s["attributes"]
        nxt = LEVELS[s["level"]] if s["level"] < len(LEVELS) else None
        stats = ", ".join(f"{SHORT[k]} {a[k]} ({modifier(a[k]):+d})" for k in ATTRIBUTES)
        lines = [
            f"Your character: level {s['level']} ({s['xp']}{f'/{nxt}' if nxt else ''} XP). {stats}."
        ]
        if s["skills"]:
            lines.append("Proficient in: " + ", ".join(sorted(s["skills"])) + ".")
        item, _, _, _ = self._weapon(agent) if hasattr(self, "_weapon") else ("", 0, 0, "")
        spec = self.config.items.get(item) if item else None
        ranged = bool(spec and spec.weapon and spec.weapon.ranged)
        hit = self._chance(
            agent, "dexterity" if ranged else "strength", "" if ranged else "athletics", 12
        )
        heal = self._chance(agent, "wisdom", "medicine", 10)
        talk = self._chance(agent, "charisma", "persuasion", 12)
        lies = self._chance(agent, "charisma", "deception", 12)
        lines.append(
            f"Your odds (dice decide): attack ≈ {hit:.0%}{f' (with your {item})' if item else ''}, "
            f"heal ≈ {heal:.0%}, persuade someone ≈ {talk:.0%}, deceive ≈ {lies:.0%}. "
            'When you speak to change someone\'s mind, add "intent": persuade, deceive or intimidate.'
        )
        return "\n".join(lines)

    def _rpg_state(self, agent: Agent) -> dict:
        if not self.rpg:
            return {}
        s = self._sheet(agent)
        return {
            "rpg": {
                "level": s["level"],
                "xp": s["xp"],
                "next": LEVELS[s["level"]] if s["level"] < len(LEVELS) else None,
                "attributes": dict(s["attributes"]),
                "skills": sorted(s["skills"]),
                "proficiency": proficiency(s["level"]),
            }
        }
