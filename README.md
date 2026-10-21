<h1 align="center">SimCore</h1>

<p align="center">
  <strong>Social simulation engine with autonomous LLM agents — running on a real map of your city</strong>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#scenarios">Scenarios</a> &bull;
  <a href="#how-it-works">How It Works</a> &bull;
  <a href="#configuration">Configuration</a> &bull;
  <a href="#llm-providers">LLM Providers</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/LLM-LiteLLM-purple" alt="LiteLLM">
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha">
</p>

---

> **"Town Square" becomes the actual square in your city. "Park" becomes a real park nearby. Every simulation is anchored to a real place — making emergent stories feel personal.**

SimCore runs autonomous agents with memories, personalities, and goals inside a scenario you define. The dashboard renders everything on a live map pulled from OpenStreetMap, centered on wherever you are. Watch a disease outbreak unfold on the streets you know. Watch market dynamics play out in real locations.

---

<!-- Replace with actual screenshot/GIF -->
![SimCore Dashboard — epidemic scenario running on a real city map](docs/screenshot.png)

---

## Quick Start

```bash
git clone https://github.com/elisonfrank/simcore
cd simcore
pip install -e ".[dev]"

# Start the frontend
cd dashboard && npm install && npm run dev &

# Run a demo (no API key needed)
cd ..
python -m simcore.cli run scenarios/epidemic/config.yaml --dashboard --demo --speed 3.0 --port 8420
```

Open `http://localhost:5173` — the dashboard asks for your location, then resolves scenario places to real POIs near you.

---

## What Makes It Different

Most agent simulations run on abstract grids. SimCore runs on the real world:

- **Real map** — Leaflet + CartoDB dark tiles, centered on your city via geolocation
- **Real places** — scenario locations (Town Square, Park, Hospital) are matched to actual OSM POIs near you via Overpass API
- **Autonomous agents** — each agent uses an LLM to observe, think, and act every tick. Parallel decisions, emergent conversations
- **Breaking moments** — live banners when significant events fire mid-simulation
- **Post-mortem narrative** — when the simulation ends, an LLM writes a chronicle of what happened, using the agents' actual actions and reflections

---

## Scenarios

Four ready-to-run scenarios included:

| Scenario | Description | Agents |
|---|---|---|
| **Outbreak** | A mysterious illness spreads. The doctor, mayor, teacher, farmer, pastor and store owner must decide between self-interest and community | 6 |
| **Marketplace** | Competing shops face a discount chain moving in. Who adapts, who collapses? | 5 |
| **Urban Life** | A day in a small city — protests, infrastructure failures, political maneuvering | 6 |
| **High School** | Social dynamics, cliques, and the pressure to fit in | 5 |

```bash
python -m simcore.cli run scenarios/epidemic/config.yaml --dashboard --demo --speed 3.0 --port 8420
```

---

## How It Works

```
Each Tick (1 simulated hour):
┌──────────────────────────────────────────────┐
│  1. Scheduled world events fire (translated  │
│     to simulation language via LLM)          │
│  2. All agents decide in parallel:           │
│     Observe → Think (LLM) → Act             │
│  3. Interactions resolve (same location only)│
│  4. Mood, energy, relationships update       │
│  5. Every 12 ticks: agents reflect           │
│  6. State broadcasts via WebSocket           │
└──────────────────────────────────────────────┘
```

### Agent Architecture

Every agent has:

- **Persona** — Name, age, profession, Big Five (OCEAN) personality traits, goals, backstory
- **Memory** — Short-term (recent events), long-term (high-importance moments), relationships (sentiment toward others), periodic LLM reflections
- **State** — Location, mood (−1 to 1), energy (0–100%), resources, current action

The LLM receives the full persona, memory context, and world observation, then decides: `move`, `speak`, `trade`, `work`, `rest`, or `observe`.

### Personality Model

Agents use the [Big Five (OCEAN)](https://en.wikipedia.org/wiki/Big_Five_personality_traits) model:

| Trait | High | Low |
|---|---|---|
| **Openness** | Curious, creative | Practical, conventional |
| **Conscientiousness** | Organized, disciplined | Flexible, spontaneous |
| **Extraversion** | Outgoing, energetic | Reserved, solitary |
| **Agreeableness** | Cooperative, trusting | Competitive, skeptical |
| **Neuroticism** | Sensitive, anxious | Calm, resilient |

---

## Configuration

Scenarios are defined in YAML. **[docs/scenario-reference.md](docs/scenario-reference.md) is the
complete reference**: every key with its type, default and range, the effects language, what the
engine already does on its own, common mistakes and a full example. It is written so you can hand
it to an LLM with an idea and get back a scenario that loads at the first try (a test keeps it in
sync with the schema). A minimal one:

```yaml
simulation:
  name: "Outbreak"
  time_step: "1 hour"
  duration: "2 days"
  seed: 77
  language: pt   # agents speak and think in this language

llm:
  model: "ollama/qwen2.5:7b"   # or gpt-4o-mini, anthropic/claude-haiku-...
  cache: true
  temperature: 0.8

environment:
  locations:
    - name: "Town Clinic"
      type: healthcare
      capacity: 20

agents:
  - name: "Dr. Amara"
    age: 38
    profession: "physician"
    personality:
      openness: 0.8
      conscientiousness: 0.9
      extraversion: 0.5
      agreeableness: 0.7
      neuroticism: 0.4
    goals:
      - "contain the outbreak"
      - "protect the vulnerable"
    starting_location: "Town Clinic"
    backstory: "The only doctor in town for the past 10 years."

events:
  scheduled:
    - tick: 12
      type: crisis
      description: "Three residents arrive at the clinic with high fever and difficulty breathing."
```

### Agent life cycle and event effects

Agents have **health** (0–1) and a **status**: `alive`, `dead` or `departed`. Health appears in
each agent's prompt; resting slowly heals light wounds. Dead agents stop acting and their body
stays where they fell (people there see it); departed agents leave the simulation.

```yaml
agents:
  - name: "Hunter"
    health: 0.8          # starts hurt (default 1.0)
    arrives_at: 5        # enters the world at tick 5 (default 0 = from the start)

events:
  scheduled:
    - tick: 7
      type: crisis
      description: "The creature attacks the farm."
      effects:
        health: {"Tomás": -0.6, "location:Plaza": -0.1, "all": -0.05}   # health ≤ 0 → dies
        kill: ["location:Farm"]          # a name, "location:<name>" or "all"
        depart: "Mateo"                  # leaves alive
        spawn:                           # new agents arrive (count numbers the names)
          - {name: "Soldier", profession: "soldier", starting_location: "Plaza", count: 2}
        farm: destroyed                  # any other key is written to global_state
```

Effects are validated when the scenario loads. The same `effects` can be sent at run time with
`POST /api/inject` (`{"description": "...", "effects": {...}}`; 422 if invalid) or over the
WebSocket. New events: `agent_spawn`, `agent_death`, `agent_depart` and `environment_change`;
`simulation_end` carries a `reason` (`completed`, `stopped`, `no_agents_left`), and a run ends
early when nobody is left.

### Items: inventory, stock, production and trade

Agents' `resources` are a real inventory. Locations can hold `stock` (anyone there can take it)
and `produces` items for whoever works there. An optional `items` catalog describes them and
what they do when used:

```yaml
items:
  medicine: {description: "first-aid kit", on_use: {health: 0.4}}   # consumed when used
  radio:    {description: "radio", consumable: false, on_use: {mood: 0.1}}
environment:
  locations:
    - {name: "Clinic", stock: {medicine: 3}}
    - {name: "Farm", produces: {food: 2}}
agents:
  - {name: "Ana", resources: {medicine: 1, money: 10}}
```

Agents decide with new actions (`item`, `amount`, `ask_item`, `ask_amount` in the JSON):
`give` (to someone here), `take` / `drop` (location stock), `use` (on oneself or someone here),
`trade` (an offer the other answers with `accept` / `decline` within 2 ticks). The engine checks
every action (you can't give what you don't have) and tells the agent why it failed. Events can
add or remove items: `effects: {items: {"location:Clinic": {medicine: 5}, "Ana": {food: -1}}}`.
New events: `item_transfer` (`kind`: give, take, drop, use, trade, produce, effect),
`trade_offer` and `trade_result`; agent action events also carry `action_type`, `target`,
`item` and `amount`.

### Conditions, scenario actions and rule-controlled agents

**Conditions** are states an agent can be in. Each one can have:
- a description the agent reads, and an appearance others notice;
- effects every tick, and a duration after which it ends or `becomes` another condition;
- contagion: through any interaction (talking included), only through blood and wounds (`via: contact`: bites, heals, fights) or by sharing a place.

A condition with `controller: rules` takes the agent away from the LLM: it acts on targets
present, seeks the place where most targets are, or roams. This suits zombies, animals or
crowds, and costs no LLM calls.

**Scenario actions** are offered in the prompt only to agents who can use them. The engine
resolves each one:
- requirements on the actor (`who`) and on the target (`target_requires`): conditions,
  professions, items;
- an item `cost`, spent even when the action fails;
- a success `chance`;
- outcomes for the target and for the actor: health, energy, mood, conditions, items, death;
- `world` updates.

```yaml
conditions:
  infected: {description: "fever and a bite", appearance: "looks feverish", per_tick: {health: -0.03},
             duration: 5, becomes: zombie, contagion: {chance: 0.15, via: contact}}
  zombie:   {appearance: "a walking corpse", controller: rules, rules: {action: bite, seek: true}}
actions:
  bite:  {who: {conditions: [zombie]}, target_requires: {not_conditions: [zombie]}, chance: 0.6,
          on_target: {health: -0.3, add_conditions: [infected]}, message: "bites {target}"}
  shoot: {who: {items: {shotgun: 1}}, target_requires: {conditions: [zombie]}, cost: {ammo: 1},
          on_target: {health: -1}, message: "shoots {target}"}
items:
  antidote: {cures: [infected]}
agents:
  - {name: "Ramón", conditions: [infected]}
```

- **Events** can `add_condition` / `remove_condition` (`{Ana: infected, "location:Plaza": panicked}`),
  and spawned agents can start with conditions.
- **Validation:** references to unknown conditions or actions are rejected when the scenario
  loads.
- **New event:** `condition_change` (`added`, `removed`, `transformed`). Custom action events carry
  `name` and `success`.
- **State:** agents expose their `conditions`, and the state includes the `conditions` and
  `actions` catalogs.
- **LLM answers:** an unknown action name is reported as a parse error.

### Needs, world rules and places with state

**Needs** (hunger, sleep, fear…):
- They grow every tick, for the agents matching `who`, and are relieved by using an item, by an
  action (optionally in a place), or by resting or working in a place.
- Once at 1 they hurt (`at_max`).
- Agents see the high ones in their prompt, with what relieves them.

**World rules** fire when every listed check in `when` holds:
- `at`, `every`, `from` / `until`;
- `global` variables (`lt`, `lte`, `gt`, `gte`, `eq`, `ne`);
- `alive` count, `count` (with a condition, without one, or in a place);
- `place` state.

What happens is described in `then` with the events' effects language. Two keys are only
useful there:
- `adjust` adds to numeric world variables;
- `end` ends the run with that message (`simulation_end` gets `reason: rule`).

`once` makes a rule fire a single time.

**Places** can be `closed`, can restrict `access` (conditions, professions, items) and can
have a `hazard` that hurts or adds a condition every tick:
- the `locations` effect changes them at run time;
- agents (rule-controlled ones too) can't walk into a place they aren't allowed in;
- prompts list closed and dangerous places.

```yaml
needs:
  hunger: {label: hunger, grows: 0.05, relief: 0.6, satisfied_by: {items: [food]}, at_max: {health: -0.03}}
  sleep:  {grows: 0.04, satisfied_by: {places: [Home]}}
rules:
  - {description: "The air goes stale", when: {every: 2}, then: {adjust: {oxygen: -0.1}}}
  - {when: {count: {condition: zombie, gte: 3}}, then: {end: "The village has fallen"}, once: true}
  - {when: {at: 12}, then: {locations: {Farm: {hazard: {label: on fire, per_tick: {health: -0.2}}}}}}
environment:
  locations:
    - {name: Church, access: {professions: [priest]}}
agents:
  - {name: Ana, needs: {hunger: 0.4}}
```

- **New events:** `need_change` (`high`, `critical`, `ok`), `place_change`, `rule_fired`.
- **State:** agents expose `needs`; places expose `closed`, `access` and `hazard`.
- **Validation:** unknown needs, places, conditions or actions are rejected on load.

### Plans, beliefs, secrets, rumors and typed relationships

**Daily plans** (`simulation.planning: true`, every `plan_every` ticks; this costs an extra LLM
call per plan):
- Each LLM-driven agent writes a short plan and sees it in its prompt.
- It marks steps done with `"plan_step_done": true`.
- It replans after a world event, a death it witnesses or of someone close, a new condition, or
  something it is told.

**Beliefs and secrets:**
- Agents start with `beliefs` (optionally `{text, true}`, the ground truth for UIs) and `secrets`.
- When speaking, an agent can add `"share": "<something>"`. The listener stores it as a belief
  told by the speaker: it inherits the truth when it matches what the speaker believes, and is
  unknown when the speaker made it up.
- Telling a secret is reported as such.
- The `rumor` effect plants a belief in agents or places.

**Typed relationships** (`relationships: {Tomás: {type: brother, sentiment: 0.7}}`) appear in
the prompt. They also weigh on what happens:
- family learns of a death even from afar and grieves hard; friends a bit; rivals not at all;
- hurting someone turns their family against the attacker.

```yaml
simulation: {planning: true, plan_every: 8}
agents:
  - name: Lucía
    relationships: {Tomás: {type: brother, sentiment: 0.7}}
    beliefs: [{text: "The well is poisoned", true: false}]
    secrets: ["I saw the beast last night"]
events:
  scheduled:
    - {tick: 4, type: rumor, description: "A rumor at the inn",
       effects: {rumor: {"location:Inn": {text: "The stranger brought the beast", true: false}}}}
```

- **New events:** `agent_plan`, `belief_shared`.
- **State:** agents expose their beliefs, their plan and `relationship_kinds`.

---

### Conversations

- **Talking to several people:** `speak`/`interact` take one or more people: `"target": "Ana"`, `"Ana, Bob"`, `"Ana y Bob"` or `"everyone"`/`"todos"`. The line reaches whoever of them is present. The agent only waits if nobody is. Speech events carry `targets: [...]`.
- **Everyone here hears it:** a `speak` line is overheard by everyone in the same place. `interact` stays private.
- **What the prompt shows:**
  - **Recent conversation here:** the last lines spoken in the place.
  - **Waiting for your answer:** who has said something to the agent that it hasn't answered yet, and how many times. The agent can answer all of them in one turn.
- **Conversations going in circles:**
  - when an agent repeats itself, it gets a note telling it to act or say something new, and it replans;
  - when it asks what someone here already asked, the note quotes that question and its answer.
- **Being ignored:** if a listener has 2 turns to answer and does something else, the speaker learns it is being ignored. It gets a note ("don't insist: ask someone else, go somewhere else or act on your own"), it remembers it, the relationship drops a little and it replans. The engine emits `speech_ignored` (`agent`, `by`, `text`, `turns`).
- **Name matching:** names are found ignoring accents, case and titles (`padre julian`, `Julián` → `Padre Julián`).

### Role-playing rules (attributes, d20 checks, experience, levels)

With `simulation.rpg: true`, agents become role-playing characters. Without it, nothing changes.

```yaml
agents:
  - name: Sargento Ortega
    attributes: {strength: 15, dexterity: 12, constitution: 14, wisdom: 12, charisma: 11}
    skills: [athletics, intimidation]
    level: 2
```

**Characters:**
- **Attributes:** strength, dexterity, constitution, intelligence, wisdom and charisma, from 1 to 20 (modifier = (value − 10) // 2). Missing ones are derived from the personality and profession.
- **Skills:** athletics, intimidation, stealth, sleight_of_hand, medicine, perception, insight, investigation, persuasion and deception. Proficiency adds +2 (+1 every 4 levels), and professions grant sensible defaults.

**Checks:** d20 + modifiers against a DC or another agent's roll. A natural 20 is a critical (it always succeeds and doubles the effect); a natural 1 is a fumble (it always fails). Checks are used for:
- **`attack`:** strength (dexterity for `weapon.ranged`) + athletics against 10 + DEX + `armor` of the items the target carries. A critical does double damage; a fumble wastes ammo or hurts the attacker.
- **`heal`:** wisdom (medicine), DC 10 with an item and 12 without. A critical heals ×1.5, a failure half, a fumble nothing.
- **Scenario actions:** `check: {attribute, skill, dc}` or opposed with `against: constitution` replaces `chance`.
- **Speech with `"intent"`:** persuade, deceive or intimidate, against the listener's insight. A lie that is seen through isn't believed, sours the relationship and, with witnesses, costs reputation.
- **`find`:** when nobody can help, a perception check (DC 12) may find tracks.

**Experience:**
- XP comes from passed checks (more for harder ones and criticals), healing someone, killing a threat, finding someone, winning a vote you called, completing plan steps and surviving the night.
- Levels come at 100, 250, 450, 700… XP. Each level adds +1 to the most-used attribute and a little health; levels 3 and 5 add a new skill (the one tried most).

**Prompt, events and state:**
- The prompt shows the character sheet and rough odds.
- Events: `dice_roll` and `level_up`.
- Each agent's state has an `rpg` entry: level, xp, next, attributes, skills and proficiency.

### Society: attack and heal, groups, votes, reputation, favors, routines, emotions

**Built-in actions:**
- **`attack` and `heal`** (also `atacar`, `matar`, `curar`, `shoot`…), in any scenario:
  - **attack** uses the agent's best weapon, from items with `weapon: {damage, chance, ammo}`, spending ammo; without one it fights with its fists. It can kill.
  - **heal** uses the best healing item the agent carries. Without one, only people in `society.healers` can heal: a small boost that removes `heal_removes` conditions.
  - A scenario action with the same name (a scenario's own `disparar`) still wins.
- **Groups:**
  - Configured in `factions: {Guardia: {goal, leader, members, rivals}}` or per agent with `faction:`.
  - **Founded by agents** (`found_group` with a name, a `goal` and people to invite), then `join`, `leave` and `expel` (leaders).
  - **The engine suggests** forming one when three or more people who like each other meet.
  - A group gets a new leader (the best reputation) when its leader dies or leaves. A group founded during the run dissolves when one person is left.
  - Attacks across groups make them rivals.
  - Effects `join`/`leave`, and rules can `count` by `faction`.
- **Votes:** `call_vote` (a question and `options`) at the place the agent is; people there `vote`. Matters in `votes:` carry effects per outcome, e.g. `{question: "¿Cerramos la pensión?", options: {sí: {locations: {Pensión: {closed: true}}}, no: {}}}`.
  A configured matter is decided once (`once: true`, a tie can be voted again) and can wait until a tick (`from: 51`). Quote `"no"`/`"yes"` keys in YAML, or they are read as booleans (handled anyway).

**Tracked by the engine:**
- **Reputation:** helping, giving and healing raise it; attacking someone who was no threat lowers it. Others see it as labels (respected / distrusted…).
- **Favors:** receiving a gift or being healed creates a debt; giving back settles it, and one unpaid for a day sours things.
- **Routines and time of day:** `routines` by agent name, profession or `*`, per `night` / `morning` / `day` / `evening`, e.g. `{médica: {night: {rest: [Pensión]}}}`. Without them, the prompt still says what people usually do at that hour.
- **Emotions:** the answer may include `"emotion"`; otherwise it comes from what happened (attacked → angry or afraid, healed → relieved…) or the mood. Others see it ("Lola looks furious").

**In events and state:** `faction_change`, `vote_called`, `vote_result`; each agent's `faction`, `reputation`, `emotion` and `owes`; and `society` (groups and open votes).

### The story of a run

- **Key moments:** they are picked from the events as they happen.
  - Scores: deaths 1.0, transformations 0.9, revealed secrets 0.8, world events and rules 0.7–0.9, arrivals, departures and reunions 0.5, rumors and people ignoring each other 0.3–0.4…
  - The strong ones (≥ 0.7) are announced with a `story_moment` event.
- **A chronicle** (optional, one LLM call per chapter):
  ```yaml
  simulation:
    story: true
    chapter_every: 24   # ticks per chapter (a day)
  ```
  - The narrator writes a titled chapter of 2–4 sentences from that period's moments (`story_chapter` event); a last chapter closes the run.
  - The post-mortem (`POST /api/postmortem/narrative`) now builds its epilogue from the chapters, the key moments and how it ended.
  - `GET /api/story` returns the chapters and the moments.

### Looking for someone

Agents don't guess where someone is: looking for them is a goal that lasts several turns.

- **Starting:** `{"action": "find", "target": "Tomás", "content": "to warn him about the well"}`, or simply
  speaking, whispering or inviting someone who isn't here (the words are kept for when they meet).
  Aliases: `buscar`, `search`, `look_for`.
- **Each turn of `find` does something useful:**
  - if there are people here who haven't been asked, it **asks them** ("Has anyone seen Tomás?", a
    public line everyone hears) and keeps what they know (where and when they last saw him);
  - otherwise it **goes to the best place not checked since**: the freshest clue (its own or someone
    else's), then where the person usually is (their starting place), then any other open place.
- **Ending:**
  - **found:** they meet, whoever moved; the prompt then reminds the seeker what they wanted to say;
  - **gave up:** after 8 ticks or when there is nowhere left to look;
  - **lost:** the person died or left.
- **In the prompt:** "You are looking for Tomás (…). Clues: Ana saw them at Iglesia (2 ticks ago).
  Already checked: Granja, Taberna. Use "find"…".
- **Events and state:** each step emits `agent_search` (`agent`, `target`, `step` = started / asked /
  moved / found / gave_up / lost, `place`, `clues`), and the state shows each agent's `search`.

### Conditions that change an agent's mind

A condition can turn an agent into someone else **while the LLM keeps deciding** for it, as a different
kind of agent with its own goals. What it can't do is fixed by the scenario and enforced by the engine:

```yaml
conditions:
  zombie:
    appearance: a body that walks and groans
    mind:
      identity: "You are no longer {name}. You are the walking dead: you only feel hunger for the living."
      goals: [Find the living, Bite them]
      speech: false        # can't speak, whisper, invite, interact, trade or give
      memories: false      # forgets who it was: no memories, relationships, beliefs or plans
      actions: [move, wait, observe, follow, bite]   # optional whitelist
```

- **The prompt changes:**
  - `identity` and `goals` replace the persona; `{name}` and `{profession}` are filled in;
  - without `memories`, the agent's past, relationships, secrets and plans leave the prompt, and it doesn't plan or reflect;
  - the action list only shows what is allowed.
- **Enforced by the engine:** anything forbidden that the model still picks becomes a wait, and the agent remembers why.
- **In the state:** each agent gets a `mind` entry with the condition, identity, goals and limits, and conditions report their `mind`.
- **`mind` vs `controller: rules`:** `rules` still exists as the cheap option, with no LLM calls (good for big hordes). A `mind` costs one LLM call per agent per tick, like anyone else.
- **Requirements:** `Requirement` also accepts `any_conditions` ("at least one of"), e.g. an action that works on a zombie *or* a horde walker.

### Coveted items: only one can win

Mark an item `visible` (everyone sees who carries it) and `drops` (it falls where its carrier dies or
leaves), and end the run with a `holds` rule: whoever keeps it N ticks in a row wins, and `{holder}`
names them in the rule's texts. Agents are told the race and who is ahead, and fight for it with
`attack`, `take`, `find` and scenario actions such as steal or poison.

```yaml
items:
  sello: {consumable: false, visible: true, drops: true}
rules:
  - when: {holds: {item: sello, ticks: 12}}
    then: {end: "{holder} keeps the seal for twelve hours and becomes lord."}
```

### Situational awareness and private talk

- **What each agent's prompt shows:**
  - what each person here did last and whether they're hurt, or that they just arrived and from where;
  - who left and where to;
  - where everyone else was last seen;
  - places it knows are empty;
  - who will hear it if it speaks.
- **How much agents know:** `simulation.awareness: local` (the default) gives them only what they saw themselves. `full` tells them where everyone is.
- **Private talk:**
  - `whisper`: like `speak`, but only the targets hear the words; others only see a whisper happen.
  - `invite`: `{"action": "invite", "target": "Bob", "place": "Barn", "content": "Come, alone"}`. The inviter goes to the place and the invitation sits in Bob's prompt for 3 ticks. Bob decides whether to go, and the inviter knows it is waiting for him. If no place is given, the first empty place the inviter knows of is used.
  - `follow`: resolved after everyone has decided, so the follower ends up wherever the leader went that tick. It respects closed places and access rules.
- **Spanish and Portuguese aliases:** `susurrar`, `invitar` and `seguir` are also accepted.

## LLM Providers

SimCore uses [LiteLLM](https://github.com/BerriAI/litellm) — any provider it supports works here:

| Provider | Example model | Key |
|---|---|---|
| **Ollama** (local) | `ollama/qwen2.5:7b` | none |
| **OpenAI** | `gpt-4o-mini` | `OPENAI_API_KEY` |
| **Anthropic** | `anthropic/claude-haiku-4-5-20251001` | `ANTHROPIC_API_KEY` |
| **Demo mode** | built-in mock | none |

```bash
# Local (Ollama)
ollama pull qwen2.5:7b
python -m simcore.cli run scenarios/epidemic/config.yaml --dashboard

# Cloud
export ANTHROPIC_API_KEY=sk-ant-...
python -m simcore.cli run scenarios/epidemic/config.yaml --dashboard --speed 2.0
```

---

## Dashboard

- **Real map** — agents rendered as avatars on actual city streets, anchored below their location pin
- **Agent inspector** — click any agent to see live mood, energy, personality, memory, relationships
- **Activity log** — live feed of every action, color-coded by type
- **Event injector** — type any event mid-simulation ("A fire breaks out at the market")
- **Breaking banner** — appears when significant events fire
- **Post-mortem** — agent arcs, relationships formed, key moments, LLM-generated narrative

---

## API

When running with `--dashboard`, SimCore exposes a REST + WebSocket API:

```
GET  /api/state                  — Full simulation state
GET  /api/agents                 — All agents
GET  /api/events?last_n=100      — Event log
GET  /api/agents/{id}             — One agent
GET  /api/environment            — Places and world variables
GET  /api/clock                  — Current tick and time
GET  /api/version                — Pinned commit (SIMCORE_REF) and the features this Simcore supports
POST /api/inject                 — Inject event {"description": "...", "location": "...", "effects": {...}}
GET  /api/story                  — Chapters and key moments so far
POST /api/postmortem/narrative   — Generate LLM narrative from run data
POST /api/language               — Switch simulation language at runtime
POST /api/control/pause|resume|stop
GET  /api/scenarios              — Built-in and saved scenarios
POST /api/scenarios              — Save a new scenario (the YAML as JSON)
GET  /api/scenarios/{id}         — One scenario
PUT  /api/scenarios/{id}         — Update it (built-ins are edited in place: an override is kept)
DELETE /api/scenarios/{id}       — Delete a saved one, or restore a built-in to its original
POST /api/scenarios/{id}/run     — Launch it {"demo": true, "speed": 2.0}
WS   /ws                         — Real-time event stream
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Engine | Python 3.11+, asyncio |
| LLM | LiteLLM (multi-provider) |
| API | FastAPI + WebSocket |
| Dashboard | React + Vite |
| Map | Leaflet + OpenStreetMap (Overpass API) |
| Storage | SQLite (aiosqlite) |
| Config | YAML + Pydantic |
| CLI | Click |

---

## Project Structure

```
simcore/
├── simcore/
│   ├── engine/      # Simulation loop, clock, event bus
│   ├── agents/      # Agent, persona, memory, actions
│   ├── world/       # Environment, locations, interactions
│   ├── llm/         # LiteLLM provider, prompts, cache, mock
│   ├── config/      # YAML loader, Pydantic schemas
│   ├── storage/     # SQLite persistence
│   ├── server/      # FastAPI + WebSocket API
│   └── cli.py
├── dashboard/       # React + Vite frontend
├── scenarios/       # marketplace, epidemic, city, school
└── tests/           # 37 tests
```

---

## Contributing

MIT licensed. PRs welcome.

```bash
git clone https://github.com/elisonfrank/simcore
cd simcore
pip install -e ".[dev]"
pytest
```

Issues, scenario ideas, and feedback welcome at [github.com/elisonfrank/simcore/issues](https://github.com/elisonfrank/simcore/issues).