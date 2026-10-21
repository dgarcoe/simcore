# Simcore scenario reference (YAML)

Everything a scenario file can contain, in one place. It is written so that you can hand it to an
LLM together with an idea ("a mining colony running out of air, 3 days, in Spanish") and get back a
YAML file that Simcore loads at the first try. Every field below is checked by
`tests/test_scenario_reference.py` against `simcore/config/schema.py`, so this page stays complete.

## Instructions for an LLM writing a scenario

- Output **one YAML document only**: no prose before or after it, no Markdown fences unless asked.
- Use only the keys documented here. Unknown keys are ignored or rejected.
- **Every name you refer to must exist**:
  - places are `environment.locations[].name`;
  - conditions are keys of `conditions`;
  - scenario actions are keys of `actions`;
  - needs are keys of `needs`;
  - agents are `agents[].name`.

  Simcore rejects the file listing every broken reference.
- Write all story text (descriptions, goals, backstories, messages) in the scenario's
  `simulation.language`. Keys and enum values stay in English (`controller: rules`, `via: place`).
- Quote vote options that look like booleans (`"yes"`, `"no"`, `"on"`, `"off"`, `"sí"`) and the routine key
  `"*"` (a bare `*` is YAML syntax). `true:` inside beliefs and rumors works unquoted.
- Prefer a few rich mechanics over many shallow ones. A good scenario has:
  - 5–12 agents with conflicting goals, secrets and relationships;
  - pressure every tick (a need, a hazard, a variable dropping);
  - scheduled events that escalate;
  - at least two endings as rules, one good and one bad.

## File layout

```yaml
simulation:      # metadata and switches (name, time, language, optional systems)
llm:             # model settings (optional)
environment:     # places and world variables
agents:          # the cast
events:          # scheduled events
items:           # item catalog (optional)
conditions:      # states: infected, wounded, zombie… (optional)
actions:         # scenario actions: bite, shoot, repair… (optional)
needs:           # hunger, sleep, fear… (optional)
rules:           # world rules: when X → effects or an ending (optional)
factions:        # groups agents start in (optional)
votes:           # matters agents can put to a vote (optional)
routines:        # what people usually do at each time of day (optional)
society:         # attack/heal and group settings (optional)
```

Only `simulation`, `environment` and `agents` are needed; everything else is optional.

## Time

- `time_step` and `duration` are texts: `"30 minutes"`, `"1 hour"`, `"2 hours"`, `"5 days"`, `"2 weeks"`.
- Ticks run `duration / time_step` times. With `1 hour`, tick 24 is the start of day 2.
- Tick 0 is midnight of day 1, so the time of day is `tick × time_step` from midnight. Night is 22–6,
  `morning` is 6–12, `day` is 12–19 and `evening` is 19–22. Plan scheduled events and rules (`at`,
  `every`, `from`, `until`) with this in mind.

## `simulation`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | text | `Untitled Simulation` | Title |
| `description` | text | `""` | One or two sentences shown in UIs |
| `time_step` | text | `1 hour` | Length of a tick |
| `duration` | text | `7 days` | Length of the run |
| `seed` | int | 42 | Random seed (same seed + demo mode = same run) |
| `language` | text | `en` | ISO code agents speak and think in (`es`, `pt`, `en`…) |
| `planning` | bool | false | Each agent writes a short plan every `plan_every` ticks (one LLM call per plan) |
| `plan_every` | int ≥ 1 | 8 | Ticks between plans |
| `story` | bool | false | A narrator writes a chapter every `chapter_every` ticks (one LLM call each) |
| `chapter_every` | int ≥ 1 | 24 | Ticks per chapter |
| `awareness` | `local` \| `full` | `local` | Agents know where others are only from what they saw (`local`) or always (`full`) |
| `rpg` | bool | false | Role-playing rules: attributes, d20 checks, experience and levels (no extra LLM calls) |

## `llm`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `provider` | text | `openai` | LiteLLM provider |
| `model` | text | `gpt-4o-mini` | E.g. `ollama/qwen2.5:7b`, `gpt-4o-mini`, `anthropic/claude-…` |
| `reflection_model` | text | none | Cheaper/bigger model for reflections and the narrator |
| `cache` | bool | true | Cache answers by prompt (set false for more variety) |
| `temperature` | float | 0.7 | |
| `max_tokens` | int | 500 | |
| `frequency_penalty` | float | 0 | 0.5–1.5 helps small local models repeat less |
| `presence_penalty` | float | 0 | |

## `environment`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `type` | text | `grid` | Leave as is |
| `size` | [w, h] | [20, 20] | Grid size for `position` |
| `locations` | list | `[]` | The places (below) |
| `global_state` | map | `{}` | World variables every agent sees (`power: true`, `oxygen: 1.0`). Rules read them and effects change them |

### `environment.locations[]`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | text | required | Unique name, referenced everywhere |
| `type` | text | `generic` | Free text: `residential`, `healthcare`, `commercial`, `public`, `religious`, `military`, `farm`, `industrial`, `outdoor`, `engineering`… (UIs use it to draw the place) |
| `position` | [x, y] | [0, 0] | Grid position; neighbours are close |
| `capacity` | int | 50 | Max people inside |
| `properties` | map | `{}` | Free data passed to UIs |
| `city` | text | `""` | Optional real-world anchor |
| `virtual` | bool | false | Not a physical place (an online forum) |
| `stock` | map item → amount ≥ 0 | `{}` | Items lying here; anyone present can take them |
| `produces` | map item → amount ≥ 0 | `{}` | Added to whoever does `work` here, per action |
| `closed` | bool | false | Nobody can enter while closed |
| `access` | Requirement | none | Only those who meet it can enter (see Requirement) |
| `hazard` | Hazard | none | Danger here every tick |

**Hazard:** `{label, per_tick, condition}`.
- `label` is the text others see, e.g. "on fire".
- `per_tick` holds `health`, `energy` and `mood` deltas applied every tick to everyone here.
- `condition` is a condition added to whoever is here.

## `agents[]`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | text | required | Unique |
| `age` | int | 30 | |
| `profession` | text | `citizen` | Also decides who can heal, routines, rpg sheet and UI look |
| `personality` | map | all 0.5 | Big Five 0..1: `openness`, `conscientiousness`, `extraversion`, `agreeableness`, `neuroticism` |
| `goals` | list of text | `[]` | What it wants |
| `starting_location` | text | first place | A place name |
| `resources` | map item → amount | `{}` | Inventory |
| `backstory` | text | `""` | |
| `health` | 0..1 | 1 | 0 is dead |
| `arrives_at` | int ≥ 0 | 0 | Tick at which it enters the world |
| `conditions` | list | `[]` | Conditions it starts with |
| `needs` | map need → 0..1 | `{}` | Initial level of scenario needs |
| `relationships` | map name → `{type, sentiment}` | `{}` | `type` is free text (brother, partner, rival, boss…); `sentiment` is −1..1 |
| `beliefs` | list | `[]` | Text, or `{text, true: false}` for a false belief (`true` is ground truth for UIs; agents never see it) |
| `secrets` | list of text | `[]` | True things it keeps to itself; it may reveal them |
| `faction` | text | `""` | Group it starts in (created if not in `factions`) |
| `attributes` | map | derived | rpg: `strength`, `dexterity`, `constitution`, `intelligence`, `wisdom`, `charisma`, each 1..20. Missing ones are derived from personality and profession |
| `skills` | list | by profession | rpg: any of `athletics`, `intimidation`, `stealth`, `sleight_of_hand`, `medicine`, `perception`, `insight`, `investigation`, `persuasion`, `deception` |
| `level` | int 1..20 | 1 | rpg: starting level |

## `events`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `scheduled` | list | `[]` | Events at a fixed tick (below) |
| `injectable` | bool | true | Whether users may inject events while it runs |

### `events.scheduled[]`

| Field | Type | Meaning |
|---|---|---|
| `tick` | int | When |
| `type` | text | Free label: `crisis`, `news`, `weather`… |
| `description` | text | What everyone learns |
| `effects` | Effects | What changes (see Effects) |
| `translations` | map lang → text | The description in other languages (otherwise it is translated by the LLM) |

## Effects

The same language is used in scheduled events (`effects`), rule consequences (`then`) and vote
outcomes (`votes.*.options.*`).

**Targets:**
- an agent name;
- `location:<place>`, meaning everyone there (for `items`, the place's stock);
- `all`, meaning every living agent.

| Key | Shape | Meaning |
|---|---|---|
| `health` | `{target: −1..1}` | Hurt or heal |
| `kill` | target or list | Die |
| `depart` | target or list | Leave the simulation alive |
| `spawn` | list of agents | New agents arrive. Any agent field, plus `count` (1..50, names get numbered) |
| `items` | `{target: {item: ±n}}` | Give or take items |
| `add_condition` | `{target: condition or list}` | |
| `remove_condition` | `{target: condition or list}` | |
| `needs` | `{target: {need: ±x}}` | |
| `locations` | `{place: {closed, hazard, access}}` | Close or open a place, start or stop (`hazard: null`) a hazard, restrict entry |
| `adjust` | `{variable: ±x}` | Add to numeric world variables |
| `rumor` | `{target: text or {text, true}}` | Plant a rumor |
| `join` | `{target: group}` | Put agents in a group (created if new) |
| `leave` | target or list | Take agents out of their group |
| `end` | text | End the run with this message |
| *anything else* | value | Set a world variable in `global_state` (`power: false`) |

## `items`

The catalog is optional: unlisted items still work, just without a description or use.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `description` | text | `""` | |
| `consumable` | bool | true | Spent when used (food), else kept (a tool) |
| `on_use` | `{health, energy, mood}` | `{}` | Change for whoever it is used on (−1..1) |
| `cures` | list of conditions | `[]` | Removed when used (an antidote) |
| `weapon` | Weapon | none | Makes the built-in `attack` stronger |
| `armor` | int 0..10 | 0 | rpg: added to the defense of whoever carries it |
| `visible` | bool | false | Everyone sees who carries it (a crown, a seal, the only key) |
| `drops` | bool | false | Falls to the ground (the place's stock) when its carrier dies or leaves, so it can be taken |

**Weapon:** `{damage, chance, ammo, ranged}`.
- `damage` is the health taken on a hit (0.4 by default).
- `chance` is the probability of hitting without rpg (0.7 by default).
- `ammo` is the item spent per attack.
- `ranged` means aimed with dexterity in rpg.

## `conditions`

States an agent can be in. The key is the condition's name.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `description` | text | `""` | What the agent itself is told ("you are infected…") |
| `appearance` | text | `""` | What others see; empty means invisible |
| `per_tick` | `{health, energy, mood}` | `{}` | Every tick |
| `duration` | int ≥ 1 | none | Ticks until it ends (or turns into `becomes`) |
| `becomes` | condition | `""` | What it turns into after `duration` (infected → zombie) |
| `contagion` | `{chance, via}` | none | `via: interaction` spreads when people interact; `via: place` spreads every tick to everyone in the same place. `chance` is 0..1 (0.2 by default) |
| `controller` | `llm` \| `rules` | `llm` | `rules`: driven by rules, no LLM calls (hordes, animals) |
| `rules` | Rules | | Used with `controller: rules` |
| `mind` | Mind | none | Used with the LLM controller: the agent becomes someone else |
| `color` | `#rrggbb` | `""` | Tint for UIs |

**Rules** (`controller: rules`):
- `action`: a scenario action used on a target present, e.g. `bite`.
- `targets`: a Requirement. By default it is people without this condition.
- `seek` (true by default): go where most targets are.
- `wander`: 0..1 (0.3 by default), the chance to roam otherwise.

If the agent has a `rules` condition, the rules win over any `mind`.

**Mind:**
- `identity`: who it is now. `{name}` and `{profession}` are replaced.
- `goals`: replace its own goals.
- `speech` (true by default): with `false` it cannot speak, whisper, invite, interact, trade or give.
- `memories` (true by default): with `false` it forgets memories, relationships, beliefs and plans.
- `actions`: if set, the only actions it may take (built-in or scenario).

## `actions`

Scenario-specific actions. The LLM is offered each one only when the agent meets `who`. Do not reuse
a built-in action name.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `description` | text | `""` | What the LLM reads ("shoot a zombie that is here") |
| `target` | `agent` \| `none` | `agent` | A person present, or no target |
| `who` | Requirement | everyone | Who can do it |
| `target_requires` | Requirement | anyone | Who can be the target |
| `chance` | 0..1 | 1 | Probability of success |
| `check` | Check | none | rpg: a d20 roll decides instead of `chance` |
| `cost` | `{item: n}` | `{}` | Spent even on failure |
| `on_target` | Outcome | | On success, to the target |
| `on_self` | Outcome | | On success, to the actor |
| `world` | map | `{}` | World variables set on success |
| `message` | text | `""` | Shown on success; `{target}` is replaced |
| `fail_message` | text | `""` | Shown on failure |

**Requirement:** every listed check must pass.
- `conditions`: must have all of these.
- `any_conditions`: must have at least one.
- `not_conditions`: must have none.
- `professions`: any of these; a substring is enough, so `médic` matches «médica».
- `items`: must carry at least `{item: n}`.

**Outcome:**
- `health`, `energy`, `mood`: −1..1.
- `add_conditions`, `remove_conditions`.
- `items`: `{item: ±n}`.
- `kill`: true.

**Check** (rpg):
- `attribute`: dexterity by default.
- `skill`: optional.
- `dc`: 1..30, 12 by default. As a guide, 10 is easy, 15 hard and 20 nearly impossible.
- `against`: the target's attribute, for an opposed roll instead of `dc`.
- `against_skill`: optional.

A natural 20 always succeeds and a natural 1 always fails.

## `needs`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `label` | text | `""` | Display name |
| `grows` | 0..1 | 0.03 | Per tick |
| `relief` | 0..1 | 0.5 | How much satisfying it lowers it |
| `satisfied_by` | `{items, actions, places}` | | Using one of these `items`, or doing one of these `actions` (rest, work or a scenario action), optionally only at these `places`. With `places` but no actions, resting or working there counts |
| `at_max` | `{health, energy, mood}` | `{}` | Per tick once it reaches 1 |
| `who` | Requirement | everyone | Who has this need (e.g. `not_conditions: [zombie]`) |

## `rules`

World rules are checked every tick, in order. All the `when` tests that are set must hold; then
`then` is applied.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `description` | text | `""` | Told to everyone when it fires (and is the ending text in UIs) |
| `when` | When | | Tests (below) |
| `then` | Effects | `{}` | What happens; `end: "…"` ends the run |
| `once` | bool | false | Fire only the first time. Use it for one-off events; endings stop the run anyway |

**When:**
- **Ticks:**
  - `at`: this exact tick;
  - `every`: every N ticks;
  - `from` / `until`: a tick window.
- **World variables:** `global: {variable: Compare}`.
- **Living agents:** `alive: Compare` tests how many agents are alive.
- **Counted agents:** `count: Compare` counts living agents. Narrow the count with any of `condition`, `not_condition`, `location`, `faction` and `item` (carrying at least one).
- **Who keeps an item:** `holds: {item, ticks, location}` is true while the same living agent has
  carried `item` for at least `ticks` ticks in a row (1 by default), optionally only while at
  `location`. The count restarts whenever it changes hands. The rule's `description` and every text
  in `then` may say `{holder}`, which is replaced by that agent's name; it also works as an effect
  target (`kill: "{holder}"`). The item must be in `items`.
- **Places:** `place: {name: {closed, hazard, occupants: Compare}}`.

**Compare:** any of `lt`, `lte`, `gt`, `gte`, `eq`, `ne`, e.g. `{lte: 0}`.

## `factions`

Groups agents start in. Agents can also found, join, leave and expel during the run.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `goal` | text | `""` | |
| `leader` | agent | first member | |
| `members` | list of agents | `[]` | |
| `rivals` | list of factions | `[]` | |
| `color` | `#rrggbb` | `""` | For UIs |

## `votes`

Matters agents can put to a vote at a place with `call_vote`. Everyone present votes.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `question` | text | required | |
| `options` | map option → Effects | `{"yes": {}, "no": {}}` | What each winning option does. **Quote the option keys** (`"sí":`, `"no":`) |
| `duration` | int ≥ 1 | 2 | Ticks it stays open |
| `from` | int ≥ 0 | 0 | Cannot be called before this tick |
| `once` | bool | true | Once decided (not a tie), it cannot be voted again |

Agents may also vote on anything they invent, without effects.

## `routines`

Habits by time of day. The key is an agent name, a profession fragment, or `*` for everyone; the
first match wins, in that order. Phases are `night`, `morning`, `day` and `evening`. Each phase maps
an action (`rest`, `work`, `observe`, `speak` or a scenario action) to the places where it happens.

```yaml
routines:
  "*": {night: {rest: [Inn]}}
  médic: {day: {work: [Hospital]}}
  Sara: {morning: {observe: [Harbour]}}
```

## `society`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `spontaneous_groups` | bool | true | Agents may found groups of their own |
| `healers` | list | médic, medic, enfermer, nurse, sanitari, veterinari, doctor, paramedic | Professions (substring) that can `heal` without an item |
| `heal_removes` | list | herido, injured, wounded, hurt | Conditions a heal removes |

## What the engine already does (no YAML needed)

Agents decide every tick with the LLM, all in parallel, and get these **built-in actions**:

| Group | Actions | What they do |
|---|---|---|
| Moving and talking | `move`, `speak`, `whisper`, `invite`, `follow`, `find` | `speak` reaches several people at once and everyone present overhears it. `whisper` is private. `invite` asks someone to come to a place. `follow` goes after someone. `find` searches for someone using clues from the people asked |
| Daily life | `work`, `rest`, `observe`, `post`, `wait` | |
| Items | `give`, `take`, `drop`, `use`, `trade`, `accept`, `decline`, `interact` | |
| Society | `attack`, `heal` | `attack` uses the best weapon or the fists. `heal` uses a healing item, or works without one for `healers` |
| Groups and votes | `found_group`, `join`, `leave`, `expel`, `call_vote`, `vote` | |

With these, the engine runs on its own:
- relationships, reputation, favors owed and emotions;
- memories and reflections;
- noticing when someone ignores you;
- beliefs passed on in conversation, lies and rumors;
- with `rpg`:
  - attacks roll against the target's defense (10 + DEX + armor);
  - heals roll WIS (medicine);
  - `intent: persuade | deceive | intimidate` when speaking is an opposed roll;
  - experience and levels.

Do not recreate these as scenario actions. Add scenario actions only for things specific to the
story (bite, repair the radio, barricade the door, brew an antidote).

## Exact behaviour (answers to common questions)

- **Contagion `via: interaction`:** each time someone speaks to, whispers to, gives to, trades with,
  uses an item on, attacks, heals or does a scenario action on someone, the condition can pass in
  **both** directions with `chance`. A carrier that cannot speak still infects whoever acts on it, and
  whoever it acts on (bites, attacks).
- **Contagion `via: place`:** every tick, every carrier rolls `chance` for every other person in the
  same place. Several carriers stack.
- **Immunity:** nobody catches a condition they have, or one that leads to a condition they already
  have (a zombie cannot get infected).
- **Curing:** removing a condition (item `cures`, `remove_conditions`, effects) also stops its
  countdown, so it never turns into `becomes`.
- **Hazard conditions:** a hazard adds its `condition` every tick to whoever is there and lacks it, so
  a cure only lasts after leaving.
- **`mind`:**
  - `speech: false` always wins, even if `actions` lists `speak`.
  - With `actions`, list every allowed action, built-ins included (`move`, `observe`, `rest`…). An
    empty list means no limit. `wait` is always allowed.
- **Scenario actions:**
  - `chance` decides. With `simulation.rpg: true` and a `check`, the roll decides and `chance` is
    ignored.
  - `world` **sets** world variables (rules see them, like any variable).
  - To add to a variable, set a flag and let a rule do the arithmetic (pattern below).
- **Needs:** `relief` is subtracted once per satisfying use or action, not per tick. `satisfied_by`
  cannot depend on world variables; to make a place stop helping, give it a `hazard` from a rule.
- **Places in effects:**
  - `locations: {X: {hazard: null}}` ends a hazard and `{access: null}` removes a restriction.
  - A new `hazard` or `access` replaces the old one.
  - `when.place` tests take booleans: `closed: true`, `hazard: false`.
- **`count` in `when`:** filters and comparison sit in one flat map:
  `count: {condition: zombie, location: Plaza, gte: 3}`.
- **Coveted items:** agents see who carries a `visible` item ("carries the seal") and are told
  every `holds` rule and how long the current holder has kept it, so they compete for it with the
  built-in `attack`, `take`, `find` and whatever scenario actions you add (steal, poison). Make the
  item `drops: true` so that killing its carrier puts it on the floor.
- **Effect targets:** they are agents, `location:<place>` or `all`, never a group. To act on a group,
  test it with `count: {faction: X, …}` and use world variables.
- **Votes:** they only open when an agent uses `call_vote` (after `from`). To force a decision at a
  tick, use a scheduled event or a rule instead.
- **Routines:** for each time of day, the first key that defines that phase wins, in this order:
  agent name, profession fragment, `"*"`. A profession routine with only `day` still uses `"*"` at
  night.
- **Time of day:** it is fixed to the clock (night 22–6…), even in a polar night or aboard a ship.
  Describe darkness in conditions or the description if the setting needs it.

## Patterns

**Add to a variable from an action** (refuel the generator):

```yaml
actions:
  repostar:
    description: echar gasoil al generador
    target: none
    who: {items: {gasoil: 1}}
    cost: {gasoil: 1}
    world: {repostado: true}
rules:
  - when: {global: {repostado: {eq: true}}}
    then: {adjust: {combustible: 0.4}, repostado: false}
```

**A timer that starts when something happens** (the march takes 12 hours):

```yaml
environment:
  global_state: {marcha: false, horas_de_marcha: 0}
rules:
  - when: {global: {marcha: {eq: true}}}
    then: {adjust: {horas_de_marcha: 1}}
  - when: {global: {horas_de_marcha: {gte: 12}}}
    then: {end: Los caminantes llegan a la base vecina.}
```

**A countdown resource** (the generator burns fuel and the cold arrives when it is empty):

```yaml
rules:
  - when: {every: 4}
    then: {adjust: {combustible: -0.1}}
  - description: El generador se para.
    when: {global: {combustible: {lte: 0}}}
    once: true
    then: {locations: {Comedor: {hazard: {label: helado, per_tick: {health: -0.03}}}}}
```

**King of the hill** (only one can win: whoever keeps the seal 12 hours in a row):

```yaml
items:
  sello: {description: el sello del conde, consumable: false, visible: true, drops: true}
actions:
  robar:
    description: quitarle el sello a alguien sin que se dé cuenta
    target_requires: {items: {sello: 1}}
    check: {attribute: dexterity, skill: sleight_of_hand, dc: 10, against: wisdom, against_skill: perception}
    on_target: {items: {sello: -1}}
    on_self: {items: {sello: 1}}
rules:
  - description: "{holder} conserva el sello doce horas y es proclamado señor."
    when: {holds: {item: sello, ticks: 12}}
    then: {end: "{holder} es el nuevo señor de Valdemora."}
```

## Common mistakes

- **Unquoted boolean-like keys:** `no:`, `yes:`, `on:` or `off:` as keys become booleans. Quote them.
- **Wrong references:** a place, condition, action, need, agent or group name that is not defined, or is spelt differently.
- **Built-in names:** a scenario action named like a built-in one (`attack`, `heal`, `find`…).
- **Wrong keys in stat maps:** `per_tick`, `on_use` and `at_max` accept only `health`, `energy` and `mood`.
- **Negative amounts:** `stock` and `produces` must be ≥ 0.
- **rpg-only fields:** `check`, `armor` and `ranged` only matter with `simulation.rpg: true`. Without it, `chance` decides.
- **Endings:** an ending rule needs a test that can actually become true (`count: {not_condition: zombie, lte: 0}`, `at: 96`…).
- **Undefined variables:** a rule testing a world variable never fires until the variable exists. Declare every variable in `environment.global_state` with its starting value.
- **LLM cost:** every agent costs one LLM call per tick; `planning` and `story` add more. For local 3B models keep it under about 8 agents.

## Testing a scenario

```bash
python -m simcore.cli run my-scenario.yaml --demo --headless --speed 0   # no LLM: checks it loads and plays
```

The demo uses a scripted mock instead of the LLM. Try a few `seed` values to check that each ending
can happen and the pacing works.

## Complete example

```yaml
simulation:
  name: Tormenta en Isla Brava
  description: Una tormenta aísla la isla; el faro se apaga, el agua escasea y alguien miente.
  time_step: 1 hour
  duration: 3 days
  seed: 7
  language: es
  story: true
  chapter_every: 12
  rpg: true

llm:
  model: ollama/qwen2.5:7b
  temperature: 0.8

environment:
  global_state: {faro_encendido: false, agua: 1.0}
  locations:
    - {name: Puerto, type: public, position: [5, 8], capacity: 20}
    - {name: Faro, type: engineering, position: [1, 1], capacity: 3, access: {professions: [farer, mecánic]}}
    - {name: Taberna, type: commercial, position: [6, 4], capacity: 15, stock: {comida: 10, agua_embotellada: 6}}
    - {name: Ermita, type: religious, position: [9, 2], capacity: 12}
    - {name: Casa del médico, type: healthcare, position: [8, 7], capacity: 6, stock: {vendas: 4}}

agents:
  - name: Rosa
    age: 52
    profession: farera
    personality: {openness: 0.4, conscientiousness: 0.9, extraversion: 0.3, agreeableness: 0.5, neuroticism: 0.3}
    goals: [Volver a encender el faro, Que nadie salga al mar]
    starting_location: Faro
    resources: {herramientas: 1}
    attributes: {strength: 12, dexterity: 13, intelligence: 14, wisdom: 15}
    skills: [perception, investigation]
    relationships: {Iñaki: {type: hermano, sentiment: 0.6}}
  - name: Iñaki
    age: 47
    profession: pescador
    personality: {openness: 0.5, conscientiousness: 0.4, extraversion: 0.7, agreeableness: 0.4, neuroticism: 0.6}
    goals: [Salvar su barca, Conseguir agua para su familia]
    starting_location: Puerto
    faction: Cofradía
    secrets: [Vendió el gasoil del generador del faro a un barco de paso]
    relationships: {Rosa: {type: hermana, sentiment: 0.6}}
  - name: Doctor Valls
    age: 61
    profession: médico
    goals: [Atender a los heridos, Racionar el agua]
    starting_location: Casa del médico
    beliefs: [{text: El agua del aljibe está contaminada, true: false}]
  - name: Marta
    age: 30
    profession: tabernera
    goals: [Que la taberna sea el refugio de todos]
    starting_location: Taberna
    resources: {comida: 4, agua_embotellada: 3}
    faction: Cofradía
  - name: Guardacostas
    profession: guardacostas
    starting_location: Puerto
    arrives_at: 60

events:
  scheduled:
    - tick: 2
      type: weather
      description: La tormenta corta la luz y el teléfono de la isla.
      effects: {luz: false}
    - tick: 20
      type: crisis
      description: Una ola arrastra la barca de Iñaki contra las rocas.
      effects:
        health: {Iñaki: -0.3}
        add_condition: {Iñaki: herido}
        rumor: {all: {text: Alguien ha robado el gasoil del faro, true: true}}

items:
  agua_embotellada: {description: botella de agua, on_use: {energy: 0.1}}
  vendas: {description: vendas, cures: [herido], on_use: {health: 0.2}}
  arpón: {description: arpón de pesca, consumable: false, weapon: {damage: 0.5, chance: 0.6, ranged: true}}

conditions:
  herido:
    description: Estás herido y te cuesta moverte.
    appearance: herido
    per_tick: {health: -0.02}
    color: "#b13e53"

actions:
  reparar_faro:
    description: reparar el generador del faro
    target: none
    chance: 0.3
    check: {attribute: intelligence, skill: investigation, dc: 14}
    who: {professions: [farer, mecánic], items: {herramientas: 1}}
    world: {faro_encendido: true}
    message: consigue encender el faro
    fail_message: no logra arrancar el generador

needs:
  sed:
    label: Sed
    grows: 0.04
    satisfied_by: {items: [agua_embotellada]}
    at_max: {health: -0.05}

rules:
  - description: El agua de la isla se acaba poco a poco.
    when: {every: 6}
    then: {adjust: {agua: -0.1}}
  - description: El faro guía al guardacostas, que rescata a todos.
    when: {global: {faro_encendido: {eq: true}}, from: 48}
    then: {end: El guardacostas llega guiado por el faro. Isla Brava se salva.}
  - description: Sin agua ni luz, la isla se rinde.
    when: {global: {agua: {lte: 0}}}
    then: {end: Se acaba el agua antes de que pase la tormenta.}

factions:
  Cofradía: {goal: Proteger las barcas, leader: Iñaki, members: [Iñaki, Marta], color: "#41a6f6"}

votes:
  racionar:
    question: ¿Racionamos el agua de la taberna?
    options:
      "sí": {items: {"location:Taberna": {comida: -2}}, racionamiento: true}
      "no": {}
    from: 12

routines:
  "*": {night: {rest: [Taberna, Ermita]}}
  médic: {day: {work: [Casa del médico]}}

society:
  healers: [médic]
```
