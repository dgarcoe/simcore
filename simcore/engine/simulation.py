"""SimulationEngine — the main simulation loop."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from simcore.agents.actions import Action, ActionType
from simcore.agents.agent import Agent, AgentState
from simcore.agents.persona import Persona, PersonalityTraits
from simcore.config.schema import AgentConfig, SimulationConfig
from simcore.engine.clock import SimClock
from simcore.engine.effects import ALL, LOCATION_PREFIX, Effects, parse_effects
from simcore.engine.events import EventBus, EventType, SimEvent
from simcore.llm.prompts import OBSERVATION_PROMPT
from simcore.llm.provider import LLMProvider
from simcore.world.environment import Environment
from simcore.world.interactions import InteractionResolver
from simcore.world.location import Location

logger = logging.getLogger("simcore")


class SimulationEngine:
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
        self.agents[agent.id] = agent
        self.environment.place_agent(agent.id, start_loc)
        return agent

    @property
    def living_agents(self) -> list[Agent]:
        return [a for a in self.agents.values() if a.alive]

    async def run(self) -> None:
        """Run the simulation until completion or stop signal."""
        self._running = True
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
        while self._running and self.clock.advance():
            if self._paused:
                await asyncio.sleep(0.1)
                self.clock.current_tick -= 1  # undo advance while paused
                continue

            await self._run_tick()

            if not self.living_agents and not any(t > self.clock.current_tick for t in self._arrivals):
                self.end_reason = "no_agents_left"
                logger.info("Nobody is left in the simulation — ending early.")
                break

            # Delay between ticks for real-time visualization
            if self.tick_delay > 0:
                await asyncio.sleep(self.tick_delay)

        if not self.end_reason:
            self.end_reason = "stopped" if not self._running else "completed"
        self._running = False
        await self.event_bus.emit(SimEvent(
            type=EventType.SIMULATION_END,
            tick=self.clock.current_tick,
            data={"llm_stats": self.llm.stats, "reason": self.end_reason},
        ))
        logger.info(f"Simulation complete. LLM stats: {self.llm.stats}")

    async def _run_tick(self) -> None:
        """Execute one simulation tick."""
        tick = self.clock.current_tick

        await self.event_bus.emit(SimEvent(
            type=EventType.TICK_START, tick=tick,
            data={"time": self.clock.format_time(), "progress": self.clock.progress},
        ))

        # New arrivals, then scheduled events
        for agent_cfg in self._arrivals.pop(tick, []):
            await self._spawn(agent_cfg, tick, cause="")
        await self._process_scheduled_events(tick)

        # Each living agent observes, decides, acts
        agent_list = self.living_agents
        self._rng.shuffle(agent_list)  # randomize order each tick

        # Gather decisions in parallel
        decisions = await asyncio.gather(*[
            self._agent_turn(agent, tick) for agent in agent_list
        ])

        # Resolve interactions
        for agent, action in zip(agent_list, decisions):
            if action and action.type in (ActionType.SPEAK, ActionType.TRADE, ActionType.INTERACT):
                target = self._find_agent_by_name(action.target)
                if target and target.alive and target.location == agent.location:
                    result = self.interaction_resolver.resolve(agent, target, action, tick)
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
        observation = self._build_observation(agent, tick, lang)
        agent.observe(observation, tick, importance=0.3)
        prompt = agent.build_prompt(observation, language=lang)
        persona_prompt = agent.persona.to_prompt()
        if lang != "en":
            from simcore.llm.prompts import language_directive
            persona_prompt = f"{persona_prompt}\n\n{language_directive(lang)}"
        action = await self.llm.decide(persona_prompt, prompt)

        # Apply action
        if action.type == ActionType.MOVE:
            target_loc = self.environment.get_location(action.target or "")
            moved = bool(target_loc) and self.environment.move_agent(
                agent.id, agent.location, action.target
            )
            if not moved:
                why = "it is full" if target_loc else "that place does not exist"
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
        elif action.type in (ActionType.SPEAK, ActionType.TRADE, ActionType.INTERACT):
            target = self._find_agent_by_name(action.target)
            if target and target.state.status == "dead" and target.location == agent.location:
                action = Action(type=ActionType.WAIT, reasoning=f"{target.name} is dead")
            elif not target or not target.alive or target.id == agent.id or target.location != agent.location:
                action = Action(type=ActionType.WAIT, reasoning="Target not present at this location")

        agent.apply_action(action, tick)

        # Emit event
        event_type = {
            ActionType.SPEAK: EventType.AGENT_SPEAK,
            ActionType.MOVE: EventType.AGENT_MOVE,
            ActionType.POST: EventType.AGENT_POST,
        }.get(action.type, EventType.AGENT_ACTION)

        await self.event_bus.emit(SimEvent(
            type=event_type, tick=tick,
            data={"action": action.describe(), "reasoning": action.reasoning},
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

    def _witnesses(self, agent: Agent) -> list[Agent]:
        return [a for a in self.living_agents if a.id != agent.id and a.location == agent.location]

    async def kill(self, agent: Agent, tick: int, cause: str = "") -> None:
        """The agent dies: it stops acting and its body stays where it fell."""
        if not agent.alive:
            return
        agent.state.status = "dead"
        agent.state.health = 0.0
        place = self.environment.get_location(agent.location)
        if place:
            place.remove_occupant(agent.id)
        text = f"{agent.name} has died{f' ({cause})' if cause else ''}."
        self.environment.log_event(tick, "death", text, location=agent.location, agents=[agent.name])
        for other in self._witnesses(agent):
            other.observe(f"I saw {agent.name} die here.", tick, importance=0.95)
            other.state.mood = max(-1.0, other.state.mood - 0.2)
            other.memory.update_relationship(agent.name, 0.0, tick, "died")
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

    def _find_agent_by_name(self, name: str | None) -> Agent | None:
        """Find an agent by name (case-insensitive)."""
        if not isinstance(name, str) or not name.strip():
            return None
        name_lower = name.lower().strip()
        for agent in self.agents.values():
            if agent.name.lower() == name_lower or agent.id == name_lower:
                return agent
        return None

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        self._running = False

    def get_state(self) -> dict:
        """Get full simulation state snapshot."""
        return {
            "clock": self.clock.to_dict(),
            "agents": {aid: a.to_dict() for aid, a in self.agents.items()},
            "pending_arrivals": sorted(
                (cfg.name for t, cfgs in self._arrivals.items() if t > self.clock.current_tick for cfg in cfgs)
            ),
            "environment": self.environment.to_dict(),
            "llm_stats": self.llm.stats,
        }
