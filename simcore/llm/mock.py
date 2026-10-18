"""Mock LLM provider — rule-based decisions for demo mode (no API key needed)."""

from __future__ import annotations

import random
import re

from simcore.agents.actions import Action, ActionType


class MockLLMProvider:
    """Generates agent decisions without calling any LLM API."""

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._call_count = 0
        self._cache_hits = 0
        self._known_locations: list[str] = []

    async def decide(self, system_prompt: str, user_prompt: str) -> Action:
        self._call_count += 1

        # Extract context clues from prompts
        is_alone = "You are alone" in user_prompt
        has_people = "People here:" in user_prompt
        location = self._extract_location(user_prompt)
        nearby = self._extract_nearby(user_prompt)
        available_locations = self._extract_locations_from_system(system_prompt)

        # Personality-driven weights
        extraversion = self._extract_trait(system_prompt, "outgoing")
        is_creative = "curious" in system_prompt.lower()
        is_hardworker = "organized" in system_prompt.lower() or "disciplined" in system_prompt.lower()

        # Pressing needs first (hunger, sleep…).
        urgent = self._need_decision(user_prompt, location)
        if urgent:
            return urgent

        # Scenario actions (bite, shoot, barricade…) when available.
        special = self._special_decision(user_prompt, nearby)
        if special:
            return special

        # Items: answer offers, pick up, use, give or offer things.
        item_action = self._item_decision(user_prompt, nearby)
        if item_action:
            return item_action

        # Decision logic
        roll = self._rng.random()

        # Extraverts seek people
        if is_alone and extraversion and roll < 0.6:
            targets = [l for l in available_locations if l != location]
            if targets:
                dest = self._rng.choice(targets)
                return Action(
                    type=ActionType.MOVE,
                    target=dest,
                    reasoning=f"It's quiet here, I should go somewhere with more people",
                )

        # Talk to nearby people
        if has_people and nearby and roll < 0.5:
            target = self._rng.choice(nearby)
            phrases = [
                f"Hey {target}, how's business today?",
                f"What do you think about the market lately?",
                f"Nice to see you here, {target}!",
                f"Have you heard any news?",
                f"Want to grab something to eat?",
                f"I think prices are going to change soon.",
                f"The weather's been interesting lately.",
                f"What are you working on?",
            ]
            action = Action(
                type=ActionType.SPEAK,
                target=target,
                content=self._rng.choice(phrases),
                reasoning=f"I want to socialize with {target}",
            )
            # Pass on something heard (or, rarely, a secret).
            heard = _list_after(user_prompt, "What you believe (not necessarily true):")
            secrets = _list_after(user_prompt, "Your secrets (")
            pick = self._rng.random()
            if secrets and pick < 0.12:
                action.share = self._rng.choice(secrets)
                action.content = "I have to tell you something."
            elif heard and pick < 0.45:
                action.share = re.sub(r" \((told by [^)]*|a rumor going around)\)$", "", self._rng.choice(heard))
                action.content = "Have you heard?"
            action.plan_step_done = "Your plan for today" in user_prompt and self._rng.random() < 0.3
            return action

        # Do business with nearby people (real exchanges of items are decided above)
        if has_people and nearby and roll < 0.65:
            target = self._rng.choice(nearby)
            deals = [
                "offering a small discount on my products",
                "looking to buy some supplies",
                "proposing a collaboration",
                "sharing some market insights for a favor",
            ]
            return Action(
                type=ActionType.INTERACT,
                target=target,
                content=self._rng.choice(deals),
                reasoning=f"Good opportunity to do business with {target}",
            )

        # Work if hardworking personality
        if is_hardworker and roll < 0.4:
            tasks = [
                "organizing inventory",
                "updating price lists",
                "cleaning up the shop",
                "analyzing today's sales",
                "preparing for tomorrow",
            ]
            return Action(
                type=ActionType.WORK,
                content=self._rng.choice(tasks),
                reasoning="I should stay productive",
            )

        # Move to a new location
        if roll < 0.5:
            targets = [l for l in available_locations if l != location]
            if targets:
                dest = self._rng.choice(targets)
                reasons = [
                    f"I feel like checking out {dest}",
                    f"Maybe something interesting is happening at {dest}",
                    f"Time for a change of scenery",
                ]
                return Action(
                    type=ActionType.MOVE,
                    target=dest,
                    reasoning=self._rng.choice(reasons),
                )

        # Observe
        if roll < 0.7:
            return Action(
                type=ActionType.OBSERVE,
                target="surroundings",
                content="looking around carefully",
                reasoning="I want to see what's going on",
            )

        # Rest
        if roll < 0.85:
            return Action(
                type=ActionType.REST,
                reasoning="I need to recharge",
            )

        # Default: wait
        return Action(
            type=ActionType.WAIT,
            reasoning="Nothing interesting to do right now",
        )

    def _need_decision(self, prompt: str, location: str) -> Action | None:
        if "Your needs: " not in prompt or self._rng.random() > 0.6:
            return None
        line = prompt.split("Your needs: ", 1)[1].split("\n", 1)[0]
        inventory = _items_after(prompt, "Your inventory: ")
        closed = _names_after(prompt, "Closed places: ")
        for relief in re.findall(r"relief: ([^)]*)\)", line):
            for way in [w.strip() for w in relief.split(",")]:
                m = re.match(r"use ([\w-]+)$", way)
                if m and m.group(1) in inventory:
                    return Action(type=ActionType.USE, item=m.group(1), reasoning=f"I need {m.group(1)}")
                m = re.match(r"(?:(rest or work)|([\w-]+)) at (.+)$", way)
                if m:
                    places = [p for p in m.group(3).split("/") if p not in closed]
                    if not places:
                        continue
                    if location in places:
                        kind = ActionType.REST if (m.group(1) or m.group(2)) in ("rest or work", "rest") else ActionType.WORK
                        if m.group(2) and m.group(2) not in ("rest", "work"):
                            return Action(type=ActionType.CUSTOM, parameters={"name": m.group(2)},
                                          reasoning="I can't hold on any longer")
                        return Action(type=kind, content="taking care of myself", reasoning="I really need this")
                    return Action(type=ActionType.MOVE, target=self._rng.choice(places), reasoning="I need to sort myself out")
                if way == "rest":
                    return Action(type=ActionType.REST, reasoning="I'm exhausted")
        return None

    def _special_decision(self, prompt: str, nearby: list[str]) -> Action | None:
        if "Special actions you can do now" not in prompt or self._rng.random() > 0.3:
            return None
        block = prompt.split("Special actions you can do now", 1)[1]
        options = re.findall(r"^- ([\w-]+) \((target: a person here|no target)", block, re.MULTILINE)
        if not options:
            return None
        name, kind = self._rng.choice(options)
        target = ""
        if kind.startswith("target"):
            if not nearby:
                return None
            target = self._rng.choice(nearby)
        return Action(type=ActionType.CUSTOM, target=target, parameters={"name": name},
                      reasoning=f"This is the moment to {name}")

    def _item_decision(self, prompt: str, nearby: list[str]) -> Action | None:
        inventory = _items_after(prompt, "Your inventory: ")
        stock = _items_after(prompt, "Items you can take here: ")
        usable = re.findall(r"(?:^|; |Item guide: )([\w-]+)(?: \([^)]*\))? — use:", prompt)
        hurt = _names_after(prompt, "Visibly hurt or sick: ")
        roll = self._rng.random()

        offers = re.findall(r"^- (.+?) offers you ", prompt, re.MULTILINE)
        if offers:
            seller = offers[0]
            if roll < 0.7:
                return Action(type=ActionType.ACCEPT, target=seller, reasoning=f"A fair deal with {seller}")
            return Action(type=ActionType.DECLINE, target=seller, reasoning="I'd rather keep what I have")

        mine_usable = [i for i in usable if i in inventory]
        if mine_usable and hurt and roll < 0.35:
            return Action(type=ActionType.USE, item=mine_usable[0], target=self._rng.choice(hurt),
                          reasoning="Someone here needs help")
        if stock and roll < 0.12:
            item = self._rng.choice(sorted(stock))
            return Action(type=ActionType.TAKE, item=item, amount=1, reasoning=f"Some {item} could be useful")
        if mine_usable and roll < 0.18:
            return Action(type=ActionType.USE, item=self._rng.choice(mine_usable), reasoning="I could use this now")
        if inventory and nearby and roll < 0.24:
            item = self._rng.choice(sorted(inventory))
            return Action(type=ActionType.GIVE, target=self._rng.choice(nearby), item=item, amount=1,
                          reasoning=f"They need {item} more than I do")
        if inventory and nearby and roll < 0.32:
            item = self._rng.choice(sorted(inventory))
            wanted = sorted((set(usable) | set(stock)) - {item})
            return Action(type=ActionType.TRADE, target=self._rng.choice(nearby), item=item, amount=1,
                          ask_item=self._rng.choice(wanted) if wanted else "", ask_amount=1,
                          reasoning=f"Let's see if I can exchange some {item}")
        return None

    async def plan(self, persona: str, situation: str, language: str = "en") -> list[str]:
        self._call_count += 1
        places = list(self._known_locations) or ["the square"]
        first, second = self._rng.sample(places, 2) if len(places) > 1 else (places[0], places[0])
        return [
            f"Go to {first} and see how things are",
            "Talk to someone I trust about what is going on",
            f"Get what I need at {second}",
            "Rest before nightfall",
        ]

    async def translate(self, text: str, language: str) -> str:
        return text  # demo mode — no translation

    async def generate_narrative(self, data: dict, language: str = "en") -> str:
        self._call_count += 1
        agents = data.get("agents", [])
        names = [a["name"] for a in agents]
        ticks = data.get("ticks", 0)
        pair = f"{names[0]} and {names[1]}" if len(names) >= 2 else (names[0] if names else "The agents")
        pair_pt = f"{names[0]} e {names[1]}" if len(names) >= 2 else (names[0] if names else "Os agentes")
        if language == "pt":
            return (
                f"Durante {ticks} ciclos de simulação, os agentes navegaram por um mundo em constante mudança. "
                f"{pair_pt} cruzaram caminhos diversas vezes, construindo laços e enfrentando desafios inesperados.\n\n"
                "As interações revelaram a complexidade das relações sociais — alguns prosperaram pela colaboração, "
                "outros preferiram a independência. No fim, cada trajetória deixou uma marca distinta no tecido social da simulação."
            )
        return (
            f"Over {ticks} ticks, the agents navigated a world in constant flux. "
            f"{pair} crossed paths repeatedly, forging connections and facing unexpected challenges along the way.\n\n"
            "The interactions revealed the complexity of social dynamics — some agents thrived through collaboration, "
            "while others charted independent courses. By the end, each trajectory had left a distinct mark on the simulation."
        )

    async def reflect(self, memories: str, language: str = "en") -> str:
        self._call_count += 1
        reflections = [
            "I've been socializing more lately. Building relationships is key to success here.",
            "The market seems competitive. I need to find my niche and focus on what I do best.",
            "I should pay more attention to what others are doing — there might be opportunities I'm missing.",
            "Today was productive. Keeping this momentum will help me reach my goals.",
            "I notice the market dynamics are shifting. I need to adapt my strategy.",
        ]
        return self._rng.choice(reflections)

    def _extract_location(self, prompt: str) -> str:
        if "You are at " in prompt:
            start = prompt.index("You are at ") + 11
            end = prompt.index(" (", start) if " (" in prompt[start:] else prompt.index(".", start)
            return prompt[start:end]
        return ""

    def _extract_nearby(self, prompt: str) -> list[str]:
        if "People here: " in prompt:
            start = prompt.index("People here: ") + 13
            end = prompt.index(".", start)
            names = prompt[start:end].split(", ")
            return [n.strip() for n in names if n.strip()]
        return []

    def _extract_trait(self, prompt: str, keyword: str) -> bool:
        return keyword.lower() in prompt.lower()

    def _extract_locations_from_system(self, prompt: str) -> list[str]:
        # Try to get locations from the environment (set by engine)
        if self._known_locations:
            return list(self._known_locations)
        return ["Town Square", "Park"]

    def set_locations(self, locations: list[str]) -> None:
        """Set known locations from the simulation engine."""
        self._known_locations = locations

    @property
    def stats(self):
        return {
            "total_calls": self._call_count,
            "cache_hits": self._cache_hits,
            "cache_hit_rate": 0.0,
            "mode": "demo",
        }


def _items_after(prompt: str, marker: str) -> dict[str, float]:
    """'Your inventory: medicine x2, food x1.' → {'medicine': 2, 'food': 1}."""
    if marker not in prompt:
        return {}
    line = prompt[prompt.index(marker) + len(marker):].split("\n", 1)[0]
    return {m[0]: float(m[1]) for m in re.findall(r"([\w-]+) x([\d.]+)", line)}


def _names_after(prompt: str, marker: str) -> list[str]:
    if marker not in prompt:
        return []
    line = prompt[prompt.index(marker) + len(marker):].split("\n", 1)[0].rstrip(".")
    return [n.strip() for n in line.split(",") if n.strip()]


def _list_after(prompt: str, marker: str) -> list[str]:
    """The "- item" lines right after a marker line."""
    if marker not in prompt:
        return []
    out = []
    for line in prompt.split(marker, 1)[1].split("\n")[1:]:
        if not line.startswith("- "):
            break
        out.append(line[2:].strip())
    return out
