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

    def _maybe_share(self, action: Action, user_prompt: str) -> Action:
        """Pass on something heard (or, rarely, a secret) while talking."""
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

    def _society_decision(self, prompt: str, nearby: list[str], location: str) -> Action | None:
        roll = self._rng.random()
        vote = re.search(r'There is a vote here: "(.+?)" \(options: (.+?)\)\.', prompt)
        if vote and roll < 0.8:
            return Action(type=ActionType.VOTE, content=self._rng.choice(vote.group(2).split(", ")),
                          reasoning=f"My say on: {vote.group(1)}")
        invite = re.search(r'invited you to join "(.+?)"', prompt)
        if invite and roll < 0.6:
            return Action(type=ActionType.JOIN, target=invite.group(1), reasoning="Better together")
        friends = re.search(r"^(.+?) think like you: you could form a group", prompt, re.MULTILINE)
        if friends and roll < 0.25:
            who = friends.group(1).replace(" and ", ", ")
            return Action(type=ActionType.FOUND_GROUP, content=f"Los de {location}" if location else "Los nuestros",
                          goal="Cuidarnos entre nosotros", target=who, reasoning="We should stick together")
        matter = re.search(r"Matters you can put to a vote here \(.*?\): (.+?)(?:;|\.$)", prompt, re.MULTILINE)
        if matter and roll < 0.2:
            return Action(type=ActionType.CALL_VOTE, content=matter.group(1).strip(), reasoning="Let's decide together")
        hurt = [n for n in _names_after(prompt, "Visibly hurt or sick: ") if n in nearby]
        if hurt and '"heal" someone' in prompt and roll < 0.3:
            return Action(type=ActionType.HEAL, target=self._rng.choice(hurt), reasoning="They need care")
        grudge = re.search(r"You feel angry towards (.+?)\.", prompt)
        if grudge and grudge.group(1) in nearby and roll < 0.1:
            return Action(type=ActionType.ATTACK, target=grudge.group(1), reasoning="I've had enough of them",
                          emotion="angry")
        routine = re.search(r"At this time you usually: (\w+)(?: at ([^;.]+))?", prompt)
        if routine and roll < 0.4:
            act, places = routine.group(1), (routine.group(2) or "").split("/")
            places = [p.strip() for p in places if p.strip()]
            if places and location not in places:
                return Action(type=ActionType.MOVE, target=places[0], reasoning=f"Time to {act}")
            if act in ("rest", "work"):
                return Action(type=ActionType(act), content="as usual", reasoning="Same as every day")
        return None

    def _search_decision(self, prompt: str, nearby: list[str]) -> Action | None:
        found = re.search(r"You found (.+?): they are here\.(?: You wanted to tell them: \"(.*?)\"\.)?", prompt)
        if found and found.group(1) in nearby:
            return Action(type=ActionType.SPEAK, target=found.group(1),
                          content=found.group(2) or f"{found.group(1)}, I was looking for you!",
                          reasoning="Finally, there they are")
        looking = re.search(r'Use "find" with target "(.+?)"', prompt)
        if looking and self._rng.random() < 0.7:
            return Action(type=ActionType.FIND, target=looking.group(1), reasoning="I have to find them")
        if not nearby and not looking and self._rng.random() < 0.05:
            people = self._extract_list(prompt, "All people who exist in this world: ")
            if people:
                who = self._rng.choice(people)
                return Action(type=ActionType.FIND, target=who, content="to see how they are doing",
                              reasoning=f"I haven't seen {who} in a while")
        return None

    def _mute_decision(self, prompt: str, nearby: list[str], location: str) -> Action:
        special = self._special_decision(prompt, nearby, chance=0.85)
        if special:
            return special
        places = re.findall(r"(?:went to|at) ([^(;,.]+?)(?: \(|;|\.)", prompt.split("Who left:", 1)[-1]
                            if "Who left:" in prompt else prompt.split("Last seen:", 1)[-1]
                            if "Last seen:" in prompt else "")
        places = [p.strip() for p in places if p.strip() and p.strip() != location]
        if not nearby and places and self._rng.random() < 0.6:
            return Action(type=ActionType.MOVE, target=self._rng.choice(places), reasoning="I smell them over there")
        movable = self._extract_list(prompt, "Available locations you can move to: ")
        if not nearby and movable and self._rng.random() < 0.5:
            return Action(type=ActionType.MOVE, target=self._rng.choice(movable), reasoning="Wandering")
        return Action(type=ActionType.WAIT, reasoning="Groaning")

    def _extract_list(self, prompt: str, marker: str) -> list[str]:
        if marker not in prompt:
            return []
        line = prompt.split(marker, 1)[1].split("\n", 1)[0].rstrip(".")
        return [x.strip() for x in line.split(",") if x.strip()]

    def _private_decision(self, user_prompt: str, nearby: list[str]) -> Action | None:
        invite = re.search(r"^(.+?) asks you to come to (.+?)(?::|\. Go there)", user_prompt, re.MULTILINE)
        if invite and self._rng.random() < 0.6:
            return Action(type=ActionType.MOVE, target=invite.group(2).strip(),
                          reasoning=f"{invite.group(1)} wants to talk to me alone")
        if not nearby:
            return None
        secrets = _list_after(user_prompt, "Your secrets (")
        roll = self._rng.random()
        if len(nearby) == 1:
            if secrets and roll < 0.25:
                return Action(type=ActionType.WHISPER, target=nearby[0], content="Keep this between us.",
                              share=self._rng.choice(secrets), reasoning="Nobody else can hear us")
            if roll > 0.96:
                return Action(type=ActionType.FOLLOW, target=nearby[0], reasoning="I'll stick with them")
            return None
        quiet = re.search(r"^Places with nobody there \(as far as you know\): (.+?)\.$", user_prompt, re.MULTILINE)
        if quiet and roll < (0.3 if secrets else 0.05):
            return Action(type=ActionType.INVITE, target=self._rng.choice(nearby),
                          place=quiet.group(1).split(", ")[0],
                          content="Come with me, I need to tell you something in private.",
                          reasoning="Too many ears here")
        return None

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

        # A changed mind that can't communicate (a zombie…): hunt, follow people, roam.
        if "You cannot speak or communicate" in user_prompt:
            return self._mute_decision(user_prompt, nearby, location)

        # A coveted item whoever keeps wins: grab it, take it from its carrier, or run with it.
        prize = self._prize_decision(user_prompt, nearby, available_locations, location)
        if prize:
            return prize

        # Pressing needs first (hunger, sleep…).
        urgent = self._need_decision(user_prompt, location)
        if urgent:
            return urgent

        # Scenario actions (bite, shoot, barricade…) when available.
        special = self._special_decision(user_prompt, nearby)
        if special:
            return special

        # Gather what a pending action needs (radio parts…) or go where it has to be done.
        gather = self._recipe_decision(user_prompt, location)
        if gather:
            return gather

        # Items: answer offers, pick up, use, give or offer things.
        item_action = self._item_decision(user_prompt, nearby)
        if item_action:
            return item_action

        # Private talk: accept invitations, take someone aside or whisper a secret.
        private = self._private_decision(user_prompt, nearby)
        if private:
            return private

        # Groups, votes, healing, grudges and routines.
        social = self._society_decision(user_prompt, nearby, location)
        if social:
            return social

        # Looking for someone: talk to them once found, keep looking, or start looking.
        search = self._search_decision(user_prompt, nearby)
        if search:
            return search

        # Answer the people waiting for a reply, all at once.
        waiting = [w.split(":", 1)[0] for w in _list_after(user_prompt, "Waiting for your answer:")]
        waiting = [w for w in waiting if w in nearby]
        if waiting and self._rng.random() < 0.7:
            replies = ["Yes, I'll look into it.", "I don't know, honestly.", "Let's deal with it now.",
                       "Good question — I'll tell you what I know.", "Not now, I'm busy."]
            return self._maybe_share(Action(
                type=ActionType.SPEAK, target=", ".join(waiting), content=self._rng.choice(replies),
                reasoning="People are waiting for my answer"), user_prompt)

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

        # Don't keep talking to people who ignore us.
        ignoring = set(re.findall(r"^Note: (.+?) has ignored you", user_prompt, re.MULTILINE))
        nearby = [n for n in nearby if n not in ignoring]

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
            if len(nearby) > 1 and self._rng.random() < 0.15:
                target = "everyone"
            action = Action(
                type=ActionType.SPEAK,
                target=target,
                content=self._rng.choice(phrases),
                reasoning=f"I want to socialize with {target}",
            )
            if "Your odds (dice decide)" in user_prompt and self._rng.random() < 0.2:
                action.intent = self._rng.choice(["persuade", "persuade", "deceive", "intimidate"])
            return self._maybe_share(action, user_prompt)

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

    def _prize_decision(self, prompt: str, nearby: list[str], places: list[str], location: str) -> Action | None:
        prizes = re.findall(r"^Whoever keeps the (.+?)(?: in .+?)? for \d+ (?:hours|turns) in a row wins", prompt,
                            re.MULTILINE)
        if not prizes:
            return None
        roll = self._rng.random()
        for prize in prizes:
            if prize in _items_after(prompt, "Items you can take here: ") and roll < 0.85:
                return Action(type=ActionType.TAKE, item=prize, amount=1,
                              reasoning=f"The {prize} is lying right there")
            if f"You carry the {prize}" in prompt or re.search(r"; you have kept it for \d+", prompt):
                others = [p for p in places if p != location]
                if nearby and others and roll < 0.35:
                    return Action(type=ActionType.MOVE, target=self._rng.choice(others),
                                  reasoning=f"Too many eyes on the {prize}")
                return None
            carriers = re.findall(rf"^- (.+?): .*carries the {re.escape(prize)}", prompt, re.MULTILINE)
            carriers = [c for c in carriers if c in nearby]
            if carriers and roll < 0.5:
                target = carriers[0]
                block = prompt.split("Special actions you can do now", 1)[1] if "Special actions you can do now" in prompt else ""
                moves = re.findall(r"^- ([\w-]+) \(target: a person here", block, re.MULTILINE)
                if moves and roll < 0.3:
                    return Action(type=ActionType.CUSTOM, target=target, parameters={"name": self._rng.choice(moves)},
                                  reasoning=f"{target} has the {prize}")
                return Action(type=ActionType.ATTACK, target=target, reasoning=f"The {prize} will be mine")
            lying = re.search(r"nobody carries it now, it lies in (.+?)\.$", prompt, re.MULTILINE)
            if lying and lying.group(1) != location and roll < 0.7:
                return Action(type=ActionType.MOVE, target=lying.group(1), reasoning=f"The {prize} is lying there")
            holder = re.search(r"; (.+?)(?: \(at (.+?)\))? has kept it for \d+", prompt)
            if holder and not carriers and roll < 0.6:
                if holder.group(2) and holder.group(2) != location:
                    return Action(type=ActionType.MOVE, target=holder.group(2),
                                  reasoning=f"{holder.group(1)} has the {prize} there")
                return Action(type=ActionType.FIND, target=holder.group(1), reasoning=f"Whoever has the {prize}…")
        return None

    def _special_decision(self, prompt: str, nearby: list[str], chance: float = 0.3) -> Action | None:
        if "Special actions you can do now" not in prompt or self._rng.random() > chance:
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
            # Prefer someone who visibly stands out (a zombie, someone bleeding…).
            noticed = re.findall(r"(?:^You notice: |; )([^;(]+?) \(", prompt.split("You notice: ", 1)[1].split("\n", 1)[0]) \
                if "You notice: " in prompt else []
            noticed = [n.strip() for n in noticed if n.strip() in nearby]
            target = self._rng.choice(noticed if noticed and self._rng.random() < 0.8 else nearby)
        return Action(type=ActionType.CUSTOM, target=target, parameters={"name": name},
                      reasoning=f"This is the moment to {name}")

    def _recipe_decision(self, prompt: str, location: str, chance: float = 0.35) -> Action | None:
        """Work towards an action listed as something to do once its items are gathered."""
        marker = "Things you could do once you have what they need"
        # Memories repeat old observations: read only the current one.
        prompt = prompt.rsplit("Current situation:", 1)[-1]
        if marker not in prompt or self._rng.random() > chance:
            return None
        block = prompt.split(marker, 1)[1].split("\n", 1)[1]
        stock = _items_after(prompt, "Items you can take here: ")
        pending = re.findall(r"^- ([\w-]+) \((.*?)\):", block, re.MULTILINE)
        for _name, details in pending:
            missing = re.findall(r"needs ([\w-]+) x", details)
            for item in missing:
                if item in stock:
                    return Action(type=ActionType.TAKE, item=item, amount=1,
                                  reasoning=f"I need {item}")
                found = re.search(rf"^  {re.escape(item)}: (.*)$", block, re.MULTILINE)
                carriers = re.findall(r"carried by ([^,]+)", found.group(1)) if found else []
                spare = sorted(set(_items_after(prompt, "Your inventory: "))
                               - set(re.findall(r"needs ([\w-]+) x", block)))
                if carriers and spare:
                    return Action(type=ActionType.TRADE, target=carriers[0], item=spare[0],
                                  amount=1, ask_item=item, ask_amount=1,
                                  reasoning=f"I need {carriers[0]}'s {item}")
                places = re.findall(r"in (.+?) x[\d.]+", found.group(1)) if found else []
                places = [p for p in places if p != location]
                if places:
                    return Action(type=ActionType.MOVE, target=self._rng.choice(places),
                                  reasoning=f"There is {item} there")
            at = re.search(r"at (.+)$", details)
            if not missing and at:
                return Action(type=ActionType.MOVE, target=at.group(1).split(" or ")[0],
                              reasoning="That's where it has to be done")
        return None

    def _item_decision(self, prompt: str, nearby: list[str]) -> Action | None:
        # What a pending action still needs is kept, not given or traded away.
        keep = set(re.findall(r"needs ([\w-]+) x", prompt))
        held = _items_after(prompt, "Your inventory: ")
        inventory = {k: v for k, v in held.items() if k not in keep}
        # Memories repeat old observations: what lies here is in the current one.
        stock = _items_after(prompt.rsplit("Current situation:", 1)[-1], "Items you can take here: ")
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

    async def chronicle(self, moments: list[str], cast: list[str], previous: list[str],
                        language: str = "en") -> tuple[str, str]:
        self._call_count += 1
        texts = [re.sub(r"^\[t\d+\] ", "", m) for m in moments]
        title = texts[-1].split(":")[0].rstrip(".")[:48] if texts else "A quiet day"
        body = ". ".join(t.rstrip(".") for t in texts[:4]) + "." if texts else "Nothing worth telling happened."
        word = {"es": "Capítulo", "pt": "Capítulo"}.get(language, "Chapter")
        return f"{word} {len(previous) + 1}: {title}", body

    async def generate_narrative(self, data: dict, language: str = "en") -> str:
        self._call_count += 1
        if data.get("chapters"):
            arc = "; then ".join(c["title"].split(": ", 1)[-1] for c in data["chapters"])
            ending = f" In the end: {data['ending']}" if data.get("ending") else ""
            return f"It began quietly; then {arc}.{ending}"
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
        found = re.search(r"You are at (.+?)(?: \(|\.(?:\s|$))", prompt)
        return found.group(1) if found else ""

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
