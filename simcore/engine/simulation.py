"""SimulationEngine — the main simulation loop."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any

from simcore.agents.actions import ITEM_ACTIONS, Action, ActionType, fmt_amount
from simcore.agents.agent import Agent, AgentState
from simcore.agents.persona import Persona, PersonalityTraits
from simcore.config.schema import AgentConfig, SimulationConfig
from simcore.engine.clock import SimClock
from simcore.engine.awareness import AwarenessMixin
from simcore.engine.conditions import ConditionsMixin
from simcore.engine.conversations import ConversationsMixin
from simcore.engine.minds import MindsMixin
from simcore.engine.search import SearchMixin
from simcore.engine.world import WorldMixin
from simcore.engine.effects import ALL, LOCATION_PREFIX, Effects, parse_effects
from simcore.engine.events import EventBus, EventType, SimEvent
from simcore.llm.prompts import OBSERVATION_PROMPT
from simcore.llm.provider import LLMProvider
from simcore.world.environment import Environment
from simcore.world.interactions import InteractionResolver
from simcore.world.location import Location

logger = logging.getLogger("simcore")


@dataclass
class TradeOffer:
    """A pending exchange: ``seller`` gives item x amount for ask_item x ask_amount."""

    seller: str  # agent id
    buyer: str  # agent id
    item: str
    amount: float
    ask_item: str
    ask_amount: float
    expires: int  # last tick it can be answered

    def describe(self, seller: str, buyer: str) -> str:
        ask = f" for {fmt_amount(self.ask_amount)} {self.ask_item}" if self.ask_item else " as a gift"
        return f"{seller} offers {buyer} {fmt_amount(self.amount)} {self.item}{ask}"


def _add(inv: dict[str, float], item: str, amount: float) -> None:
    left = inv.get(item, 0) + amount
    if left > 1e-9:
        inv[item] = left
    else:
        inv.pop(item, None)


def _has(inv: dict[str, float], item: str, amount: float) -> bool:
    return bool(item) and inv.get(item, 0) + 1e-9 >= amount


class SimulationEngine(ConditionsMixin, WorldMixin, MindsMixin, ConversationsMixin, AwarenessMixin,
                       SearchMixin):
    """Core simulation engine that orchestrates agents, environment, and time."""

    def __init__(self, config: SimulationConfig, demo_mode: bool = False,
                 tick_delay: float = 0.0):
        self.config = config
        self.clock = SimClock.from_config(config.time_step, config.duration)
        self.event_bus = EventBus()
        self.environment = self._build_environment()
        self.agents: dict[str, Agent] = {}
        # Agents that enter later (arrives_at > 0), by tick.
        self._arrivals: dict[int, list[AgentConfig]] = {}
        self.end_reason = ""
        self.end_message = ""
        self._fired_rules: set[int] = set()
        self._init_conversations()
        self._init_awareness()
        self._init_search()
        self._offers: list[TradeOffer] = []
        self.tick_delay = tick_delay  # seconds between ticks for visualization

        if demo_mode:
            from simcore.llm.mock import MockLLMProvider
            self.llm = MockLLMProvider(seed=config.seed)
        else:
            self.llm = LLMProvider(
                model=config.llm.model,
                reflection_model=config.llm.reflection_model,
                temperature=config.llm.temperature,
                max_tokens=config.llm.max_tokens,
                cache_enabled=config.llm.cache,
                frequency_penalty=config.llm.frequency_penalty,
                presence_penalty=config.llm.presence_penalty,
            )
        self.interaction_resolver = InteractionResolver()
        self._running = False
        self._paused = False
        # The tick being computed, so stop() can interrupt it instead of waiting for the LLM.
        self._tick_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._rng = random.Random(config.seed)
        # True when scenario has at least one virtual location (enables POST action)
        self._has_virtual = any(
            loc.virtual or loc.type == 'virtual'
            for loc in self.environment.locations.values()
        )

        self._build_agents()

        # Pass location names to mock provider if in demo mode
        if demo_mode and hasattr(self.llm, 'set_locations'):
            self.llm.set_locations(self.environment.get_location_names())

    def _build_environment(self) -> Environment:
        env = Environment(
            width=self.config.environment.size[0],
            height=self.config.environment.size[1],
            global_state=dict(self.config.environment.global_state),
        )
        for loc_cfg in self.config.environment.locations:
            env.add_location(Location(
                name=loc_cfg.name,
                type=loc_cfg.type,
                position=loc_cfg.position,
                capacity=loc_cfg.capacity,
                city=loc_cfg.city,
                virtual=loc_cfg.virtual,
                properties=dict(loc_cfg.properties),
                stock=dict(loc_cfg.stock),
                produces=dict(loc_cfg.produces),
                closed=loc_cfg.closed,
                access=loc_cfg.access,
                hazard=loc_cfg.hazard,
            ))
        return env

    def _build_agents(self) -> None:
        for agent_cfg in self.config.agents:
            if agent_cfg.arrives_at > 0:
                self._arrivals.setdefault(agent_cfg.arrives_at, []).append(agent_cfg)
            else:
                self._create_agent(agent_cfg)

    def _unique_name(self, name: str) -> str:
        taken = {a.name.lower() for a in self.agents.values()}
        if name.lower() not in taken:
            return name
        n = 2
        while f"{name} {n}".lower() in taken:
            n += 1
        return f"{name} {n}"

    def _create_agent(self, agent_cfg: AgentConfig, name: str | None = None) -> Agent:
        """Build an agent from its config and place it in the world."""
        name = self._unique_name(name or agent_cfg.name)
        persona = Persona(
            name=name,
            age=agent_cfg.age,
            profession=agent_cfg.profession,
            personality=PersonalityTraits(
                openness=agent_cfg.personality.openness,
                conscientiousness=agent_cfg.personality.conscientiousness,
                extraversion=agent_cfg.personality.extraversion,
                agreeableness=agent_cfg.personality.agreeableness,
                neuroticism=agent_cfg.personality.neuroticism,
            ),
            goals=tuple(agent_cfg.goals),
            backstory=agent_cfg.backstory,
        )
        # Redirect to first physical location if starting_location is virtual or unknown
        start_loc = agent_cfg.starting_location
        loc_obj = self.environment.locations.get(start_loc)
        physical = [l.name for l in self.environment.locations.values() if not l.virtual and l.type != 'virtual']
        if loc_obj and (loc_obj.virtual or loc_obj.type == 'virtual'):
            logger.warning(f"Agent {name} starts at virtual '{start_loc}' → redirected to '{physical[0] if physical else start_loc}'")
            start_loc = physical[0] if physical else start_loc
        elif loc_obj is None and physical:
            start_loc = physical[0]

        state = AgentState(
            location=start_loc,
            resources=dict(agent_cfg.resources),
            health=agent_cfg.health,
        )
        agent_id = name.lower().replace(" ", "_")
        while agent_id in self.agents:
            agent_id += "_"
        agent = Agent(persona=persona, state=state, agent_id=agent_id)
        for name in agent_cfg.conditions:
            cond = self.config.conditions.get(name)
            if cond:
                agent.state.conditions[name] = cond.duration
        for name in self.config.needs:
            agent.state.needs[name] = max(0.0, min(1.0, float(agent_cfg.needs.get(name, 0.0))))
        self._init_mind(agent, agent_cfg, self.clock.current_tick)
        self.agents[agent.id] = agent
        self.environment.place_agent(agent.id, start_loc)
        return agent

    @property
    def living_agents(self) -> list[Agent]:
        return [a for a in self.agents.values() if a.alive]

    async def run(self) -> None:
        """Run the simulation until completion or stop signal."""
        self._running = True
        self._stop_event.clear()
        logger.info(f"Starting simulation: {self.config.name}")
        logger.info(f"  Agents: {len(self.agents)}")
        logger.info(f"  Locations: {len(self.environment.locations)}")
        logger.info(f"  Duration: {self.clock.max_ticks} ticks")

        await self.event_bus.emit(SimEvent(
            type=EventType.SIMULATION_START,
            tick=0,
            data={"config": self.config.name, "agents": len(self.agents)},
        ))

        self.end_reason = ""
        self.end_message = ""
        while self._running and self.clock.advance():
            if self._paused:
                await asyncio.sleep(0.1)
                self.clock.current_tick -= 1  # undo advance while paused
                continue

            self._tick_task = asyncio.ensure_future(self._run_tick())
            try:
                await self._tick_task
            except asyncio.CancelledError:
                if self._running:  # cancelled from outside, not by stop(): propagate
                    raise
                logger.info(f"Stopped in the middle of tick {self.clock.current_tick}.")
                break
            finally:
                self._tick_task = None

            if not self.living_agents and not any(t > self.clock.current_tick for t in self._arrivals):
                self.end_reason = "no_agents_left"
                logger.info("Nobody is left in the simulation — ending early.")
                break

            # Delay between ticks for real-time visualization (a stop ends it at once)
            if self.tick_delay > 0:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), self.tick_delay)
                except asyncio.TimeoutError:
                    pass

        if not self.end_reason:
            self.end_reason = "stopped" if not self._running else "completed"
        self._running = False
        await self.event_bus.emit(SimEvent(
            type=EventType.SIMULATION_END,
            tick=self.clock.current_tick,
            data={"llm_stats": self.llm.stats, "reason": self.end_reason, "message": self.end_message},
        ))
        logger.info(f"Simulation complete. LLM stats: {self.llm.stats}")

    async def _run_tick(self) -> None:
        """Execute one simulation tick."""
        tick = self.clock.current_tick

        await self.event_bus.emit(SimEvent(
            type=EventType.TICK_START, tick=tick,
            data={"time": self.clock.format_time(), "progress": self.clock.progress},
        ))

        await self._expire_offers(tick)

        # New arrivals, then scheduled events
        for agent_cfg in self._arrivals.pop(tick, []):
            await self._spawn(agent_cfg, tick, cause="")
        await self._process_scheduled_events(tick)
        if self.config.conditions:
            await self._condition_tick(tick)
        if self.config.needs:
            await self._needs_tick(tick)
        await self._hazards_tick(tick)
        if self.config.rules:
            await self._rules_tick(tick)
            if not self._running:  # a rule ended the run
                await self.event_bus.emit(SimEvent(type=EventType.TICK_END, tick=tick, data=self.get_state()))
                return

        # Each living agent observes, decides, acts
        agent_list = self.living_agents
        self._rng.shuffle(agent_list)  # randomize order each tick

        # Gather decisions in parallel
        self._see_around(tick)
        await self._searches_tick(tick)
        decisions = await asyncio.gather(*[
            self._agent_turn(agent, tick) for agent in agent_list
        ])
        await self._resolve_follows(tick)

        # Resolve interactions
        for agent, action in zip(agent_list, decisions):
            if action and action.type in (ActionType.SPEAK, ActionType.INTERACT, ActionType.WHISPER):
                said = action if action.type != ActionType.WHISPER else Action(
                    type=ActionType.SPEAK, target=action.target, content=action.content)
                for target in self._addressees(agent, action):
                    result = self.interaction_resolver.resolve(agent, target, said, tick)
                    # Skip INTERACTION event for SPEAK — AGENT_SPEAK already
                    # emitted the same line, no need to duplicate.
                    if result and action.type != ActionType.SPEAK:
                        await self.event_bus.emit(SimEvent(
                            type=EventType.INTERACTION, tick=tick,
                            data={"result": result.description, "content": action.content},
                            source=agent.name,
                        ))

        # Per-tick passive decay for living agents
        for agent in self.living_agents:
            agent.apply_tick_decay()

        # Periodic reflection (every 12 ticks = ~12 hours at 1h/tick)
        if tick % 12 == 0 and tick > 0:
            await self._run_reflections(tick)

        await self.event_bus.emit(SimEvent(
            type=EventType.TICK_END, tick=tick,
            data=self.get_state(),
        ))

    async def _agent_turn(self, agent: Agent, tick: int) -> Any:
        """Run one agent's observation → decision → action cycle."""
        lang = getattr(self.config, "language", "en")
        controller = self._controller(agent)
        if controller is not None:
            # Rule-controlled (a zombie, an animal, a crowd): no LLM call.
            action = self._rule_decision(agent, controller)
        else:
            observation = self._build_observation(agent, tick, lang)
            if self.config.planning:
                had = (agent.state.plan_tick, agent.state.plan_step)
                await self._maybe_plan(agent, observation, tick, lang)
                if (agent.state.plan_tick, agent.state.plan_step) != had:
                    observation = self._build_observation(agent, tick, lang)
            agent.observe(observation, tick, importance=0.3)
            found = self._mind(agent)
            mind = found[1] if found else None
            prompt = agent.build_prompt(observation, language=lang, mind=mind)
            persona_prompt = agent.persona_prompt(mind)
            if lang != "en":
                from simcore.llm.prompts import language_directive
                persona_prompt = f"{persona_prompt}\n\n{language_directive(lang)}"
            action = await self.llm.decide(persona_prompt, prompt)
            action = self._enforce_mind(agent, action, tick)

        # Apply action
        origin = agent.location
        if action.type == ActionType.MOVE:
            target_loc = self.environment.get_location(action.target or "")
            allowed, why = self._can_enter(agent, target_loc) if target_loc else (True, "")
            moved = bool(target_loc) and allowed and self.environment.move_agent(
                agent.id, agent.location, action.target
            )
            if not moved:
                why = why or ("it is full" if target_loc else "that place does not exist")
                agent.observe(f"I tried to go to {action.target} but couldn't: {why}.", tick, importance=0.5)
                action = Action(type=ActionType.WAIT,
                                reasoning=f"Could not move to {action.target}: {why}. {action.reasoning}".strip())
        elif action.type == ActionType.POST:
            if self._has_virtual and action.content:
                # Broadcast post as a global event — visible to all agents next tick
                description = f"{agent.name} postou nas redes sociais: {action.content}"
                self.environment.log_event(tick, "social_post", description, location="")
                for other in self.living_agents:
                    if other.id != agent.id:
                        other.observe(description, tick, importance=0.6)
            else:
                action = Action(type=ActionType.WAIT, reasoning="No social network available")
        elif action.type in ITEM_ACTIONS:
            action = await self._apply_item_action(agent, action, tick)
        elif action.type == ActionType.CUSTOM:
            action = await self._apply_custom_action(agent, action, tick)
        elif action.type == ActionType.FIND:
            action = await self._apply_find(agent, action, tick)
        elif (action.type in (ActionType.SPEAK, ActionType.WHISPER, ActionType.INVITE)
              and (absent := self._absent_person(agent, action.target))):
            # Talking to someone who isn't here: go and find them instead of wasting the turn.
            said = action.content
            action = await self._apply_find(
                agent, Action(type=ActionType.FIND, target=absent.name, reasoning=action.reasoning,
                              content=f"to tell them: {said}" if said else ""), tick, message=said)
        elif action.type == ActionType.INVITE:
            action = await self._apply_invite(agent, action, tick)
        elif action.type == ActionType.FOLLOW:
            action = self._apply_follow(agent, action, tick)
        elif action.type in (ActionType.SPEAK, ActionType.INTERACT, ActionType.WHISPER):
            action = self._check_speech(agent, action)
            if action.type != ActionType.WAIT:
                self._record_speech(agent, action, tick)
                self._search_answered(agent, list(action.parameters.get("targets") or []))

        agent.apply_action(action, tick)
        if action.type == ActionType.MOVE and agent.location != origin:
            self._note_move(agent, origin, agent.location, tick)
        self._note_doing(agent, action, tick)
        await self._check_ignored(agent, action, tick)
        if action.type == ActionType.WORK:
            await self._produce(agent, tick)
        await self._satisfy_by_action(agent, action, tick)
        await self._after_action_minds(agent, action, tick)
        if self.config.conditions and action.target and action.type in (
                ActionType.SPEAK, ActionType.INTERACT, ActionType.WHISPER, ActionType.GIVE,
                ActionType.TRADE, ActionType.ACCEPT, ActionType.USE, ActionType.CUSTOM):
            for other in self._addressees(agent, action):
                await self._spread_by_interaction(agent, other, tick)

        # Emit event
        event_type = {
            ActionType.SPEAK: EventType.AGENT_SPEAK,
            ActionType.MOVE: EventType.AGENT_MOVE,
            ActionType.POST: EventType.AGENT_POST,
        }.get(action.type, EventType.AGENT_ACTION)

        await self.event_bus.emit(SimEvent(
            type=event_type, tick=tick,
            data={
                "action": action.describe(),
                "reasoning": action.reasoning,
                # Structured copy of the action (the text above stays for older clients).
                "action_type": action.type.value,
                "target": action.target,
                "content": action.content,
                **({"targets": action.parameters["targets"]} if action.parameters.get("targets") else {}),
                **({"place": action.place, "from": action.parameters.get("from", "")}
                   if action.type == ActionType.INVITE else {}),
                **({"step": action.parameters.get("step", ""), "place": action.parameters.get("place", ""),
                    "from": action.parameters.get("from", "")}
                   if action.type == ActionType.FIND else {}),
                **({"item": action.item, "amount": action.amount} if action.item else {}),
                **({"name": action.parameters.get("name"), "success": action.parameters.get("success", True)}
                   if action.type == ActionType.CUSTOM and action.parameters.get("name") else {}),
            },
            source=agent.name,
        ))

        return action

    _SOCIAL_HINT = {
        "pt": 'Redes sociais ativas. Use a ação "post" para publicar publicamente a qualquer momento — sua mensagem chegará a todos imediatamente.',
        "es": 'Redes sociales activas. Usa la acción "post" para publicar públicamente en cualquier momento.',
        "en": 'Social networks are active. Use the "post" action to publish publicly at any time — your message reaches everyone immediately.',
    }

    def _build_observation(self, agent: Agent, tick: int, lang: str = "en") -> str:
        """Build what an agent currently observes."""
        location = self.environment.get_location(agent.location)
        if not location:
            return "You are in an unknown location."

        nearby = self.environment.get_nearby_agents(agent.location, agent.id)
        nearby_names = [
            self.agents[aid].name for aid in nearby if aid in self.agents
        ]
        recent_events = self.environment.get_recent_events(tick, window=3)
        event_descriptions = [
            e["description"] for e in recent_events
            if e.get("location") == agent.location or e.get("location") == ""
        ]

        available_locations = self.environment.get_location_names()
        known_agents = [a.name for a in self.living_agents if a.id != agent.id]
        observation = OBSERVATION_PROMPT.render(
            location_name=location.name,
            location_type=location.type,
            agents_here=nearby_names,
            available_locations=available_locations,
            known_agents=known_agents,
            recent_events=event_descriptions[-5:],
            global_context=str(self.environment.global_state) if self.environment.global_state else "",
            social_network_hint=self._SOCIAL_HINT.get(lang, self._SOCIAL_HINT["en"]) if self._has_virtual else "",
        )
        stock = {k: v for k, v in location.stock.items() if v > 0}
        if stock:
            observation += "\nItems you can take here: " + ", ".join(
                f"{k} x{fmt_amount(v)}" for k, v in stock.items()) + "."
        if location.produces:
            observation += "\nWorking here produces: " + ", ".join(
                f"{k} x{fmt_amount(v)}" for k, v in location.produces.items()) + "."
        offers = [o for o in self._offers if o.buyer == agent.id]
        if offers:
            observation += "\nOffers waiting for your answer (accept or decline, target = who offers):"
            for o in offers:
                observation += f"\n- {o.describe(self.agents[o.seller].name, 'you')}"
        if self.config.conditions or self.config.actions:
            extra = self._conditions_prompt(agent)
            if extra:
                observation += "\n" + extra
        found = self._mind(agent)
        forgets = bool(found) and not found[1].memories
        mute = bool(found) and not found[1].speech
        for extra in (self._needs_prompt(agent), self._places_prompt(),
                      "" if forgets else self._minds_prompt(agent),
                      self._awareness_prompt(agent, tick, speech=not mute),
                      "" if forgets else self._search_prompt(agent, tick),
                      "" if mute else self._conversation_prompt(agent, tick)):
            if extra:
                observation += "\n" + extra
        guide = self._item_guide(set(agent.state.resources) | set(stock))
        if guide:
            observation += f"\nItem guide: {guide}."
        bodies = [a.name for a in self.agents.values() if a.state.status == "dead" and a.location == agent.location]
        if bodies:
            observation += f"\nDead bodies here: {', '.join(bodies)}."
        hurt = [a.name for a in self.living_agents
                if a.id != agent.id and a.location == agent.location and a.state.health < 0.5]
        if hurt:
            observation += f"\nVisibly hurt or sick: {', '.join(hurt)}."
        return observation

    async def _run_reflections(self, tick: int) -> None:
        """Run periodic reflection for all agents."""
        lang = getattr(self.config, "language", "en")
        for agent in self.living_agents:
            found = self._mind(agent)
            if self._controller(agent) is not None or (found and not found[1].memories):
                continue  # rule-controlled agents and minds that forgot who they were don't reflect
            memories = agent.memory.get_context(max_entries=10)
            if memories:
                summary = await self.llm.reflect(memories, language=lang)
                if summary:
                    agent.memory.add_reflection(summary)
                    await self.event_bus.emit(SimEvent(
                        type=EventType.REFLECTION, tick=tick,
                        data={"summary": summary},
                        source=agent.name,
                    ))

    async def _process_scheduled_events(self, tick: int) -> None:
        """Process events scheduled for this tick."""
        lang = getattr(self.config, "language", "en")
        for event_cfg in self.config.events.scheduled:
            if event_cfg.tick == tick:
                description = event_cfg.translations.get(lang) or event_cfg.description
                if lang != "en" and not event_cfg.translations.get(lang):
                    description = await self.llm.translate(description, lang)
                logger.info(f"Scheduled event at tick {tick}: {description}")
                self.environment.log_event(tick, event_cfg.type, description)
                for agent in self.living_agents:
                    agent.observe(f"World event: {description}", tick, importance=0.8)
                    self._wake(agent)
                await self.event_bus.emit(SimEvent(
                    type=EventType.SCHEDULED_EVENT, tick=tick,
                    data={"description": description, "effects": event_cfg.effects},
                ))
                await self.apply_effects(parse_effects(event_cfg.effects), tick, cause=description)

    async def inject_event(self, description: str, location: str = "",
                           effects: dict | None = None) -> None:
        """Inject an event into the simulation at runtime (effects are validated first)."""
        parsed = parse_effects(effects)
        tick = self.clock.current_tick
        if description:
            self.environment.log_event(tick, "injected", description, location)
            for agent in self.living_agents:
                if not location or agent.location == location:
                    agent.observe(f"Breaking event: {description}", tick, importance=0.9)
                    self._wake(agent)

            await self.event_bus.emit(SimEvent(
                type=EventType.INJECTED_EVENT, tick=tick,
                data={"description": description, "location": location, "effects": effects or {}},
            ))
        await self.apply_effects(parsed, tick, cause=description)

    # ── Effects and life cycle ────────────────────────────────────────

    def _resolve_targets(self, spec: str) -> list[Agent]:
        """'all', 'location:<name>' or an agent name → living agents."""
        spec = spec.strip()
        if spec.lower() == ALL:
            return self.living_agents
        if spec.lower().startswith(LOCATION_PREFIX):
            where = spec[len(LOCATION_PREFIX):].strip().lower()
            return [a for a in self.living_agents if a.location.lower() == where]
        agent = self._find_agent_by_name(spec)
        if agent is None:
            logger.warning(f"Effect target '{spec}' matches no agent or location")
        return [agent] if agent and agent.alive else []

    async def apply_effects(self, effects: Effects, tick: int, cause: str = "") -> None:
        """Apply an event's effects: world state, health, deaths, departures, arrivals."""
        if effects.empty:
            return
        if effects.global_updates:
            self.environment.global_state.update(effects.global_updates)
            await self.event_bus.emit(SimEvent(
                type=EventType.ENVIRONMENT_CHANGE, tick=tick,
                data={"global_state": dict(effects.global_updates), "cause": cause},
            ))
        for spec, delta in effects.health.items():
            for agent in self._resolve_targets(spec):
                agent.state.health = max(0.0, min(1.0, agent.state.health + delta))
                if delta < 0:
                    agent.observe(f"I got hurt{f' ({cause})' if cause else ''}.", tick, importance=0.8)
                    if agent.state.health <= 0:
                        await self.kill(agent, tick, cause)
        for spec, changes in effects.items.items():
            await self._apply_item_effect(spec, changes, tick, cause)
        if effects.adjust:
            state = self.environment.global_state
            for key, delta in effects.adjust.items():
                try:
                    state[key] = round(float(state.get(key, 0) or 0) + delta, 6)
                except (TypeError, ValueError):
                    logger.warning(f"Can't adjust non-numeric world variable '{key}'")
            await self.event_bus.emit(SimEvent(
                type=EventType.ENVIRONMENT_CHANGE, tick=tick,
                data={"global_state": {k: state.get(k) for k in effects.adjust}, "cause": cause},
            ))
        for name, change in effects.locations.items():
            await self._change_place(name, change, tick, cause)
        for spec, changes in effects.needs.items():
            for agent in self._resolve_targets(spec):
                for name, delta in changes.items():
                    if name in self.config.needs:
                        await self._set_need(agent, name, agent.state.needs.get(name, 0.0) + delta, tick, cause)
        for spec, names in effects.remove_condition.items():
            for agent in self._resolve_targets(spec):
                for name in names:
                    await self.remove_condition(agent, name, tick, cause)
        for spec, names in effects.add_condition.items():
            for agent in self._resolve_targets(spec):
                for name in names:
                    await self.add_condition(agent, name, tick, cause)
        for spec in effects.kill:
            for agent in self._resolve_targets(spec):
                await self.kill(agent, tick, cause)
        for spec in effects.depart:
            for agent in self._resolve_targets(spec):
                await self.depart(agent, tick, cause)
        for spawn in effects.spawn:
            for i in range(spawn.count):
                name = f"{spawn.name} {i + 1}" if spawn.count > 1 else spawn.name
                await self._spawn(spawn, tick, cause, name=name)
        for spec, (text, truth) in effects.rumor.items():
            for agent in self._resolve_targets(spec):
                await self._learn(agent, text, "rumor", truth, tick)
        if effects.end:
            self.request_end("rule", effects.end)

    def _witnesses(self, agent: Agent) -> list[Agent]:
        return [a for a in self.living_agents if a.id != agent.id and a.location == agent.location]

    async def kill(self, agent: Agent, tick: int, cause: str = "") -> None:
        """The agent dies: it stops acting and its body stays where it fell."""
        if not agent.alive:
            return
        agent.state.status = "dead"
        agent.state.health = 0.0
        self._drop_offers(agent)
        place = self.environment.get_location(agent.location)
        if place:
            place.remove_occupant(agent.id)
        text = f"{agent.name} has died{f' ({cause})' if cause else ''}."
        self.environment.log_event(tick, "death", text, location=agent.location, agents=[agent.name])
        for other in self._witnesses(agent):
            other.observe(f"I saw {agent.name} die here.", tick, importance=0.95)
            other.state.mood = max(-1.0, other.state.mood - 0.2 - self._witness_grief(other, agent))
            self._wake(other)
            other.memory.update_relationship(agent.name, 0.0, tick, "died")
        await self._grieve(agent, tick, cause)
        await self.event_bus.emit(SimEvent(
            type=EventType.AGENT_DEATH, tick=tick,
            data={"agent": agent.name, "id": agent.id, "location": agent.location, "cause": cause},
            source=agent.name,
        ))

    async def depart(self, agent: Agent, tick: int, cause: str = "") -> None:
        """The agent leaves the simulation alive."""
        if not agent.alive:
            return
        agent.state.status = "departed"
        self._drop_offers(agent)
        place = self.environment.get_location(agent.location)
        if place:
            place.remove_occupant(agent.id)
        text = f"{agent.name} has left{f' ({cause})' if cause else ''}."
        self.environment.log_event(tick, "departure", text, location=agent.location, agents=[agent.name])
        for other in self._witnesses(agent):
            other.observe(text, tick, importance=0.7)
        await self.event_bus.emit(SimEvent(
            type=EventType.AGENT_DEPART, tick=tick,
            data={"agent": agent.name, "id": agent.id, "location": agent.location, "cause": cause},
            source=agent.name,
        ))

    async def _spawn(self, agent_cfg: AgentConfig, tick: int, cause: str = "",
                     name: str | None = None) -> Agent:
        """A new agent enters the world."""
        agent = self._create_agent(agent_cfg, name=name)
        where = agent.location
        self.environment.log_event(tick, "arrival", f"{agent.name} ({agent.persona.profession}) has arrived.",
                                   location=where, agents=[agent.name])
        agent.observe(f"I have just arrived at {where}.{f' Context: {cause}' if cause else ''}", tick, importance=0.8)
        for other in self._witnesses(agent):
            other.observe(f"{agent.name}, a {agent.persona.profession}, has arrived here.", tick, importance=0.7)
        await self.event_bus.emit(SimEvent(
            type=EventType.AGENT_SPAWN, tick=tick,
            data={"agent": agent.name, "id": agent.id, "location": where, "cause": cause,
                  "profession": agent.persona.profession},
            source=agent.name,
        ))
        return agent

    # ── Items ─────────────────────────────────────────────────────────

    def _item_guide(self, names: set[str]) -> str:
        parts = []
        for name in sorted(names):
            item = self.config.items.get(name)
            if not item:
                continue
            use = ", ".join([f"{k} {'+' if v >= 0 else ''}{v:g}" for k, v in item.on_use.items()]
                            + [f"cures {c}" for c in item.cures])
            text = name + (f" ({item.description})" if item.description else "")
            if use:
                text += f" — use: {use}{'' if item.consumable else ', reusable'}"
            parts.append(text)
        return "; ".join(parts)

    async def _transfer(self, tick: int, kind: str, item: str, amount: float, source: str = "",
                        to: str = "", location: str = "") -> None:
        await self.event_bus.emit(SimEvent(
            type=EventType.ITEM_TRANSFER, tick=tick,
            data={"kind": kind, "item": item, "amount": amount, "from": source, "to": to,
                  "location": location},
            source=source or to,
        ))

    async def _refuse(self, agent: Agent, action: Action, tick: int, why: str) -> Action:
        agent.observe(f"I tried to {action.describe()} but couldn't: {why}.", tick, importance=0.5)
        return Action(type=ActionType.WAIT, reasoning=f"Couldn't {action.describe()}: {why}. {action.reasoning}".strip())

    def _present(self, agent: Agent, name: str) -> Agent | None:
        other = self._find_agent_by_name(name)
        if other and other.alive and other.id != agent.id and other.location == agent.location:
            return other
        return None

    async def _apply_item_action(self, agent: Agent, action: Action, tick: int) -> Action:
        """Checks and applies give/take/drop/use/trade/accept/decline. Returns the action
        actually done (WAIT with the reason when it isn't possible)."""
        inv = agent.state.resources
        place = self.environment.get_location(agent.location)
        item, amount = action.item, action.amount
        kind = action.type

        if kind in (ActionType.GIVE, ActionType.DROP, ActionType.TRADE) and not _has(inv, item, amount):
            return await self._refuse(agent, action, tick, f"I don't have {fmt_amount(amount)} {item or 'of that'}")

        if kind == ActionType.GIVE:
            other = self._present(agent, action.target)
            if not other:
                return await self._refuse(agent, action, tick, f"{action.target or 'nobody'} is not here")
            _add(inv, item, -amount)
            _add(other.state.resources, item, amount)
            other.observe(f"{agent.name} gave me {fmt_amount(amount)} {item}.", tick, importance=0.7)
            other.memory.update_relationship(agent.name, 0.1, tick, f"gave me {item}")
            agent.memory.update_relationship(other.name, 0.03, tick, f"I gave {item}")
            agent.state.mood = min(1.0, agent.state.mood + 0.03)
            await self._transfer(tick, "give", item, amount, agent.name, other.name, agent.location)
            return action

        if kind == ActionType.TAKE:
            if not place or not _has(place.stock, item, amount):
                return await self._refuse(agent, action, tick, f"there is no {fmt_amount(amount)} {item or 'such item'} here")
            _add(place.stock, item, -amount)
            _add(inv, item, amount)
            await self._transfer(tick, "take", item, amount, f"location:{place.name}", agent.name, place.name)
            return action

        if kind == ActionType.DROP:
            if not place:
                return await self._refuse(agent, action, tick, "there is nowhere to leave it")
            _add(inv, item, -amount)
            _add(place.stock, item, amount)
            await self._transfer(tick, "drop", item, amount, agent.name, f"location:{place.name}", place.name)
            return action

        if kind == ActionType.USE:
            spec = self.config.items.get(item)
            if not _has(inv, item, 1):
                return await self._refuse(agent, action, tick, f"I don't have any {item or 'item'}")
            if not spec or not (spec.on_use or spec.cures):
                return await self._refuse(agent, action, tick, f"{item} has no use")
            on = agent
            if action.target and action.target.strip().lower() not in (agent.name.lower(), "self", "me", "myself"):
                on = self._present(agent, action.target)
                if not on:
                    return await self._refuse(agent, action, tick, f"{action.target} is not here")
            if spec.consumable:
                _add(inv, item, -1)
            for key, delta in spec.on_use.items():
                low = -1.0 if key == "mood" else 0.0
                setattr(on.state, key, max(low, min(1.0, getattr(on.state, key) + delta)))
            for name in spec.cures:
                await self.remove_condition(on, name, tick, f"{item} from {agent.name}")
            await self._satisfy_by_item(on, item, tick)
            if on is not agent:
                on.observe(f"{agent.name} used {item} on me.", tick, importance=0.7)
                on.memory.update_relationship(agent.name, 0.1, tick, f"used {item} on me")
            await self._transfer(tick, "use", item, 1 if spec.consumable else 0, agent.name, on.name, agent.location)
            action.target = "" if on is agent else on.name
            return action

        if kind == ActionType.TRADE:
            other = self._present(agent, action.target)
            if not other:
                return await self._refuse(agent, action, tick, f"{action.target or 'nobody'} is not here")
            offer = TradeOffer(agent.id, other.id, item, amount, action.ask_item, action.ask_amount,
                               expires=tick + 2)
            self._offers = [o for o in self._offers if not (o.seller == agent.id and o.buyer == other.id)]
            self._offers.append(offer)
            other.observe(offer.describe(agent.name, "me") + ".", tick, importance=0.7)
            await self.event_bus.emit(SimEvent(
                type=EventType.TRADE_OFFER, tick=tick,
                data={"from": agent.name, "to": other.name, "item": item, "amount": amount,
                      "ask_item": action.ask_item, "ask_amount": action.ask_amount if action.ask_item else 0,
                      "expires": offer.expires},
                source=agent.name,
            ))
            return action

        # ACCEPT / DECLINE
        seller = self._find_agent_by_name(action.target)
        offer = next((o for o in self._offers if seller and o.seller == seller.id and o.buyer == agent.id), None)
        if not offer:
            return await self._refuse(agent, action, tick, f"{action.target or 'nobody'} has no offer for me")
        self._offers.remove(offer)
        if kind == ActionType.DECLINE:
            seller.observe(f"{agent.name} declined my offer.", tick, importance=0.6)
            await self._trade_result(offer, False, "declined", tick)
            return action
        if seller.location != agent.location or not seller.alive:
            await self._trade_result(offer, False, "not together any more", tick)
            return await self._refuse(agent, action, tick, f"{seller.name} is not here any more")
        if not _has(seller.state.resources, offer.item, offer.amount):
            await self._trade_result(offer, False, f"{seller.name} no longer has it", tick)
            return await self._refuse(agent, action, tick, f"{seller.name} no longer has {offer.item}")
        if offer.ask_item and not _has(inv, offer.ask_item, offer.ask_amount):
            await self._trade_result(offer, False, f"{agent.name} can't pay", tick)
            return await self._refuse(agent, action, tick, f"I don't have {fmt_amount(offer.ask_amount)} {offer.ask_item}")
        _add(seller.state.resources, offer.item, -offer.amount)
        _add(inv, offer.item, offer.amount)
        await self._transfer(tick, "trade", offer.item, offer.amount, seller.name, agent.name, agent.location)
        if offer.ask_item:
            _add(inv, offer.ask_item, -offer.ask_amount)
            _add(seller.state.resources, offer.ask_item, offer.ask_amount)
            await self._transfer(tick, "trade", offer.ask_item, offer.ask_amount, agent.name, seller.name,
                                 agent.location)
        seller.observe(f"{agent.name} accepted my offer.", tick, importance=0.7)
        # Mood and relationship bump of a completed deal.
        self.interaction_resolver.resolve(seller, agent, Action(
            type=ActionType.TRADE, target=agent.name,
            content=offer.describe(seller.name, agent.name)), tick)
        await self._trade_result(offer, True, "", tick)
        return action

    async def _trade_result(self, offer: TradeOffer, accepted: bool, reason: str, tick: int) -> None:
        await self.event_bus.emit(SimEvent(
            type=EventType.TRADE_RESULT, tick=tick,
            data={"from": self.agents[offer.seller].name, "to": self.agents[offer.buyer].name,
                  "item": offer.item, "amount": offer.amount, "ask_item": offer.ask_item,
                  "ask_amount": offer.ask_amount, "accepted": accepted, "reason": reason},
            source=self.agents[offer.buyer].name,
        ))

    async def _expire_offers(self, tick: int) -> None:
        for offer in [o for o in self._offers if o.expires < tick]:
            self._offers.remove(offer)
            await self._trade_result(offer, False, "expired", tick)

    def _drop_offers(self, agent: Agent) -> None:
        self._offers = [o for o in self._offers if agent.id not in (o.seller, o.buyer)]

    async def _produce(self, agent: Agent, tick: int) -> None:
        place = self.environment.get_location(agent.location)
        if not place or not place.produces:
            return
        for item, amount in place.produces.items():
            _add(agent.state.resources, item, amount)
            await self._transfer(tick, "produce", item, amount, f"location:{place.name}", agent.name, place.name)

    async def _apply_item_effect(self, spec: str, changes: dict[str, float], tick: int, cause: str) -> None:
        """Effects ``items``: add/remove items for agents or a location's stock."""
        if spec.lower().startswith(LOCATION_PREFIX):
            where = spec[len(LOCATION_PREFIX):].strip()
            place = next((p for p in self.environment.locations.values() if p.name.lower() == where.lower()), None)
            if not place:
                logger.warning(f"Effect target '{spec}' matches no location")
                return
            for item, delta in changes.items():
                delta = max(delta, -place.stock.get(item, 0))
                if delta:
                    _add(place.stock, item, delta)
                    await self._transfer(tick, "effect", item, abs(delta), "" if delta > 0 else spec,
                                         spec if delta > 0 else "", place.name)
            return
        for agent in self._resolve_targets(spec):
            for item, delta in changes.items():
                delta = max(delta, -agent.state.resources.get(item, 0))
                if not delta:
                    continue
                _add(agent.state.resources, item, delta)
                verb = "received" if delta > 0 else "lost"
                agent.observe(f"I {verb} {fmt_amount(abs(delta))} {item}{f' ({cause})' if cause else ''}.",
                              tick, importance=0.6)
                await self._transfer(tick, "effect", item, abs(delta), "" if delta > 0 else agent.name,
                                     agent.name if delta > 0 else "", agent.location)

    def _find_agent_by_name(self, name: str | None) -> Agent | None:
        """Find an agent by name (case-insensitive)."""
        if not isinstance(name, str) or not name.strip():
            return None
        name_lower = name.lower().strip()
        for agent in self.agents.values():
            if agent.name.lower() == name_lower or agent.id == name_lower:
                return agent
        return self._match_agent(name)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        """Stop now: the tick in progress (agents waiting on the LLM) is cancelled."""
        self._running = False
        self._stop_event.set()
        if self._tick_task is not None and not self._tick_task.done():
            self._tick_task.cancel()

    def _agent_state(self, agent: Agent) -> dict:
        data = agent.to_dict()
        search = self._search_state(agent)
        if search:
            data["search"] = search
        found = self._mind(agent)
        if found:
            name, mind = found
            p = agent.persona
            data["mind"] = {"condition": name, "goals": list(mind.goals), "speech": mind.speech,
                            "memories": mind.memories,
                            "identity": mind.identity.replace("{name}", p.name).replace("{profession}", p.profession)}
        return data

    def get_state(self) -> dict:
        """Get full simulation state snapshot."""
        return {
            "clock": self.clock.to_dict(),
            "agents": {aid: self._agent_state(a) for aid, a in self.agents.items()},
            "items": {name: item.model_dump() for name, item in self.config.items.items()},
            "conditions": {
                name: {"description": c.description, "appearance": c.appearance, "color": c.color,
                       "controller": c.controller, "becomes": c.becomes, "duration": c.duration,
                       "mind": c.mind.model_dump() if c.mind else None}
                for name, c in self.config.conditions.items()
            },
            "actions": {
                name: {"description": a.description, "target": a.target}
                for name, a in self.config.actions.items()
            },
            "needs": {name: {"label": n.label} for name, n in self.config.needs.items()},
            "offers": [
                {"from": self.agents[o.seller].name, "to": self.agents[o.buyer].name, "item": o.item,
                 "amount": o.amount, "ask_item": o.ask_item, "ask_amount": o.ask_amount, "expires": o.expires}
                for o in self._offers
            ],
            "pending_arrivals": sorted(
                (cfg.name for t, cfgs in self._arrivals.items() if t > self.clock.current_tick for cfg in cfgs)
            ),
            "environment": self.environment.to_dict(),
            "llm_stats": self.llm.stats,
        }
