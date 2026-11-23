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
| `pause_each_day` | bool | false | Pause at the end of each day (an `intermission` event); games with players use it so players read their day and change their agent's orders before the DM resumes |
| `exhaustion` | bool | true | An agent whose energy is spent (≤ 0.02) can only rest: it collapses and rests that turn without asking the LLM (+0.3 energy). Energy drains a little every tick, more when working or fighting; its prompt warns it below 0.15. `false`: only the warning |
| `exhaustion_place` | string | `""` | Where a collapsed agent is carried to recover (a hospital, home): it wakes up there with half its energy. Empty: it rests where it fell |
| `exhaustion_recovery` | int ≥ 0 | 0 | More turns a collapsed agent spends recovering (resting, out of play, like `knockout.recovery`) |
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
| `max_tokens` | int | 500 | Answers cut off at this limit are flagged (`truncated` on the action event); raise it for long speeches |
| `frequency_penalty` | float | 0 | 0.5–1.5 helps small local models repeat less |
| `num_ctx` | int ≥ 0 | 8192 | Ollama only: context window in tokens. Ollama's default (2048–4096) cuts long prompts from the start; 0 keeps it |
| `record_prompts` | bool | false | Keep each decision's prompt and raw answer in the run's recording (`decision_trace` events, not sent live) to see in the replay why an agent did something. Big: for debugging |
| `max_concurrency` | int ≥ 0 | 0 | At most this many LLM calls at once (0 = no limit); with many agents and a local server (Ollama), set it near `OLLAMA_NUM_PARALLEL` so calls don't time out waiting |
| `presence_penalty` | float | 0 | |

## `environment`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `type` | text | `grid` | Leave as is |
| `size` | [w, h] | [20, 20] | Grid size for `position` |
| `locations` | list | `[]` | The places (below) |
| `global_state` | map | `{}` | World variables every agent sees (`power: true`, `oxygen: 1.0`). Rules read them and effects change them. Keys starting with `ui_` are for the UI and never reach a prompt: `ui_theme`, and `ui_words` (`{group: crew, territory: sector}`; plurals optional) for what the UI calls groups and territories |

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
| `territory` | bool | false | Groups fight over it (see `territories`) |
| `income` | `{item: n}` | `{}` | A territory pays this into its group's stash every `territories.every` ticks |
| `market` | `{currency, sells, buys, stock, hours, elasticity, recovery}` | none | A market: agents here use the actions `buy` and `sell` (with `item` and `amount`, up to 5). `sells: {item: price}` is what it sells and its base price, `buys: {item: price}` what it pays; `currency` (default `economy.currency`). Every unit bought makes the item `elasticity` (0.15) dearer here and every unit sold that much cheaper; prices drift back to the base by `recovery` (0.1) each tick. `stock: {item: n}` limits what it sells (sales to it refill it); unlisted items never run out. `hours: [20, 6]` opens it only from 20:00 to 06:00. Agents here see its prices (and "pricey/cheap now"), others where the other markets are; events can change prices (`prices`) or open and close it (`markets`). Each trade is a `market_trade` event |

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
| `orders` | Orders | none | What the player who configured the agent tells it to do to win (below) |

**Orders** (games where each player configures an agent): the agent reads them every turn, first
and above everything else.
- `style`: aggressive, negotiator, sneaky, loyal, opportunist, businessman (or `agresivo`,
  `negociador`, `sigiloso`, `leal`, `oportunista`, `empresario`), told with what it means; any other
  word is told as is.
- `priorities`: what matters most, in order (e.g. "que gane mi banda", "ser la leyenda").
- `risk`: `careful`, `bold` or `reckless` (how good the odds must be to fight or steal).
- `red_lines` (up to 5): `{action, target}` it never does, enforced (the action is refused): a
  built-in or scenario action, optionally only against someone (`my_group` = members of its own
  group); `{action: leave}` = never leaving its group.
- `plans` (up to 5): `{when, then}`; `then` is an instruction (up to 200 characters) told as
  "ACTIVE NOW" while every test in `when` holds: `day` (from this day on), `group_rank` and
  `my_rank` (Compare on its group's / its own place, 1 = first), `carries`, `health` (Compare),
  `knocked_out` (bool), `group_territories` (Compare).
- `text`: free text up to 400 characters.

An active plan or a red line that stops an action is reported as an `orders` event.

Everything a player writes (style, priorities, plans, text, and in a game their agent's name,
profession, backstory, goals and secrets) is cleaned before it reaches a prompt: control characters
go, quotes and braces are replaced, and what reads like an instruction to the model ("ignore the
rules above", "system prompt", "respond only with…", chat-template tags) is replaced by `[…]`. The
agent reads it quoted as `«…»`, introduced as its player's wishes for the character, never new rules.

At the end of every run `simulation_end` carries `awards` (`[{key, agent, value}]`: knockouts,
brawler, punching_bag, thief, talker, wanderer, lucky, unlucky, turncoat, tempted, schemer, pacifist,
untouchable; people only, not agents driven by rules) and `highlights` (each agent's three most
important moments: `{tick, kind, importance, …}`).

```yaml
orders:
  style: sigiloso
  priorities: [ser la leyenda, que gane mi banda]
  risk: careful
  red_lines: [{action: attack, target: my_group}, {action: leave}]
  plans:
    - {when: {day: 3, group_rank: {gte: 3}}, then: "Pásate a la banda que vaya primera"}
    - {when: {carries: maletin}, then: "Escóndete en Las Torres con el maletín"}
  text: "Nada de peleas a lo loco: palos limpios y a casa."
```

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
| `items` | `{target: {item: ±n}}` | Give or take items (`"*": -n`: n at random). Items put in a place are announced to those there, and to everyone if the item is `visible` |
| `add_condition` | `{target: condition or list}` | |
| `remove_condition` | `{target: condition or list}` | |
| `needs` | `{target: {need: ±x}}` | |
| `locations` | `{place: {closed, hazard, access}}` | Close or open a place, start or stop (`hazard: null`) a hazard, restrict entry |
| `adjust` | `{variable: ±x}` | Add to numeric world variables |
| `rumor` | `{target: text or {text, true}}` | Plant a rumor |
| `join` | `{target: group}` | Put agents in a group (created if new) |
| `leave` | target or list | Take agents out of their group |
| `prices` | `{place: {item: factor}}` | Multiply an item's price at a market (`2` doubles it: scarcity; `0.5` halves it) |
| `markets` | `{place: {open: true\|false}}` | Open or close a market, overriding its hours |
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
| `curse` | Curse | none | Cursed: see below |
| `fuse` | `{ticks, on_holder, on_others, message}` | none | A fuse (dynamite, a bomb): lit when an agent picks the item up, it goes off `ticks` later wherever the item is. The fuse keeps burning when the item is given, stolen or dropped. When it goes off the unit is spent, `on_holder` (an outcome: `health`, `energy`, `mood`, `add_conditions`, `kill`…) hits whoever carries it and `on_others` everyone else at the place; `message` (`{item}`, `{place}`) is what everyone there hears. Holders and people nearby see "DANGER: a lit … goes off in N ticks" in their prompt; the event is `item_fuse` |
| `bonus` | `{key: n}` | `{}` | Helps whoever carries it with the dice (−5…+5): keys are an attribute (`dexterity`), a skill (`sleight_of_hand`), an action (`attack`, `heal`, a scenario action) or `defense`. The best of each item counts and all items add up, at most +5 per roll. Without `rpg`, each point is +5% on that action's chance (`attack`, `defense` and scenario actions). Rolls show it (`item_bonus`, `items`) and agents read it in their item guide |

**Curse** (a cursed item): whoever carries it gets `condition` (give the condition `per_tick`
harm or a `mind` that changes how they think) until the item is gone. With `sticky: true` it
can't be given, dropped or traded away (it can still be stolen, and it falls with its carrier if
`drops`). `broken_by: [item or action…]` breaks it: using that item on the carrier, or doing that
scenario action to (or, without a target, by) the carrier; the item crumbles and the condition
goes. `description` is what the carrier learns when the curse takes hold. Carriers are told their
item is cursed and what breaks it; nobody else is.

```yaml
conditions:
  poseido: {description: "solo piensas en el anillo", appearance: "mirada perdida",
            per_tick: {mood: -0.05}, mind: {goals: ["Que nadie te quite el anillo"]}}
items:
  anillo_negro: {description: "un anillo de hierro negro", consumable: false,
                 curse: {condition: poseido, sticky: true, broken_by: [agua_bendita, exorcizar]}}
  agua_bendita: {description: "agua bendita"}
```

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
| `contagion` | `{chance, via}` | none | `via: interaction` spreads when people deal with each other (talking included); `via: contact` only through blood and wounds (bites, heals, fights, scenario actions); `via: place` spreads every tick to everyone in the same place. `chance` is 0..1 (0.2 by default) |
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

Scenario-specific actions. The LLM is offered each one only when the agent meets `who`, carries the
`cost` and stands at one of the `where` places. Do not reuse a built-in action name.

Each turn the actions the agent can do now are listed with who they can be done to and their odds
(`golpear (target: Marcos; success ≈ 65%)`), and their names lead the options of the answer's
`"action"`. Names written loosely (`Reparar radio`, `reparar_radio()`, `golpear_a_marcos`) and free
text that starts with the action's verb ("Reparo la radio") are read as the action; free text that
names no action does nothing, and the agent is told so.

When the agent meets `who` except for its items, or is somewhere else, the action is listed as
something they could do once they gather what it needs. The list says what is missing, where it lies
(place stocks) and who here carries it. Each item also says which actions need it.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `description` | text | `""` | What the LLM reads ("shoot a zombie that is here") |
| `target` | `agent` \| `none` | `agent` | A person present, or no target |
| `who` | Requirement | everyone | Who can do it |
| `target_requires` | Requirement | anyone | Who can be the target |
| `chance` | 0..1 | 1 | Probability of success |
| `check` | Check | none | rpg: a d20 roll decides instead of `chance` |
| `cost` | `{item: n}` | `{}` | Spent even on failure |
| `where` | list of places | `[]` (anywhere) | Only at these places (a workshop, the radio tower) |
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
- `own_territory: true`: standing in a territory its group holds (protection rackets, taxes).

**Outcome:**
- `health`, `energy`, `mood`: −1..1.
- `add_conditions`, `remove_conditions`.
- `items`: `{item: ±n}`. `"*": -n` loses n items at random among what they carry.
- `kill`: true.
- `steal` (in `on_target`): `one` (an item at random) or `all` (everything they carry) goes to
  whoever does the action; `steal_except: [item…]` are never taken this way.

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
- **Points:** `score: Compare` is true when some living agent's points pass it (see `scores`).
- **Alternatives:** `any: [When, …]` is true when at least one of those sets of tests holds (each
  written like `when`), on top of the other tests in this `when`. Use it for several ways to win
  in one rule; or write several rules with `end`.

**Winner:** in a rule that ends the run, `{winner}` in `description` and `then` is replaced by who
won: whoever kept the item (`holds`), reached the points (`score`, the best of them), the group
counted (`count.faction`); otherwise the last one alive, otherwise the best score (ties joined with
`&`). The run's end reports it as `winner`, with the final points.

**Compare:** any of `lt`, `lte`, `gt`, `gte`, `eq`, `ne`, e.g. `{lte: 0}`.

**Two winners:** `{mvp}` in an ending is the best individual score (dead or alive), so an
ending can name a winning group and a winning person: `end: "{winner} se quedan con la ciudad; la
leyenda es {mvp}."`. A rule's `winner: group` makes `{winner}` the best group (its members'
points plus `territories.group_points` per territory held) and `winner: person` the best score,
when no test names one. The run's end reports `winner` and `mvp`.

## `territories`

Places with `territory: true` are fought over by groups. A group's strength at a place is its
members there (agents with the `society.knockout` condition don't count). A place nobody holds,
or one with none of its owner's members there, goes at once to the strongest group there (a tie
leaves it contested). A held place with rivals in it is contested: if one rival group outnumbers
the owner's members there for `siege` ticks in a row it takes the place (a tie keeps the owner and
restarts the siege); the owner's members are warned at each step. A contested place pays
`contested_income` of its income; a place nobody stands in keeps its owner. Agents see who holds each
territory, how many their group holds and what each pays. `when: {territories: {faction, gte,
ticks}}` is true while a group (any, or `faction`) holds at least `gte` of them for `ticks` ticks
in a row; `{winner}` is that group.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `every` | int ≥ 1 | 4 | Ticks between incomes (`income` of each held territory into its group's stash) |
| `group_points` | int | 0 | Points per territory held added to a group's score (the group ranking) |
| `siege` | int ≥ 1 | 2 | Ticks in a row a rival group must outnumber the owner's members to take a guarded place |
| `contested_income` | 0–1 | 0.5 | Share of its income a place pays while rivals are in it |

```yaml
territories: {every: 4, group_points: 5}
environment:
  locations:
    - {name: El Puerto, territory: true, income: {mercancia: 2}}
rules:
  - when: {territories: {gte: 5, ticks: 6}}
    then: {end: "{winner} se quedan con la ciudad. La leyenda es {mvp}."}
```

## `economy`

Money, at the top level of the YAML (like `territories`):

```yaml
economy: {currency: dinero, group_wealth_points: 0.25}
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `currency` | item | `dinero` | The money item: markets' default currency, `when.wealth` and the "magnate" award |
| `group_wealth_points` | number ≥ 0 | 0 | Points a group gets per unit of currency in its common stash (group ranking) |

Markets are set per place (`environment.locations[].market`). Without any market, money is just
another item agents give, trade and steal.

## `scores`

Points and achievements. With `visible: true` (default) every agent sees its points, the ranking
and what earns or loses points, so they go for them.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `visible` | bool | true | Agents see the points and the achievements |
| `achievements` | map name → Achievement | `{}` | |

**Achievement:** `description` (what agents read), `points` (negative for penalties), `when`
(below) and `repeat` (false: earned once; true: earned again each time it happens again).

**Achievement `when`** (one of):
- `action: name` with `times: n` (default 1): doing a built-in action (`heal`, `move`, `give`…) or
  a scenario action (only when it succeeds) that many times. With `repeat`, each `times` more.
- `visited: all` or `visited: [place…]`: having been at every place, or at those.
- `carries: item`: carrying it. `condition: name`: having it. With `repeat`, again each `every`
  ticks (default 24) that it lasts.
- `knocks_out: true`: knocking someone out (or killing them) in a fight (with `repeat`, each time).
- `wealth: n`: carrying at least `n` of `economy.currency` (once).
- `alive_at_end: true`: still alive when the run ends.
- `captures: true`: being there when your group takes a territory (with `repeat`, each time).
- `in_territory: true`: being in a territory your group holds (with `repeat`, each `every` ticks).
- `without: action` with `every: n`: a penalty each `n` ticks without doing that action (use a
  negative `points`).

```yaml
scores:
  achievements:
    explorador: {description: "Visitar todos los lugares", points: 10, when: {visited: all}}
    curandero: {description: "Curar a tres personas", points: 5, when: {action: heal, times: 3}}
    mecanico: {description: "Cada reparación", points: 2, repeat: true, when: {action: reparar_radio}}
    superviviente: {description: "Seguir vivo al final", points: 10, when: {alive_at_end: true}}
    vago: {description: "Un día sin trabajar", points: -3, when: {without: work, every: 24}}
rules:
  - when: {score: {gte: 30}}
    then: {end: "{winner} llega a 30 puntos y gana la feria."}
```

## `factions`

Groups agents start in. Agents can also found, join, leave and expel during the run.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `goal` | text | `""` | |
| `leader` | agent | first member | |
| `members` | list of agents | `[]` | |
| `rivals` | list of factions | `[]` | |
| `color` | `#rrggbb` | `""` | For UIs |

Groups change during the run. Anyone may `leave` a group, configured ones included (a configured
group lasts while it has members). Agents are nudged to: leave when they distrust the leader; found
a new group with two or more people here they get on well with, even if that means leaving theirs;
and `join` a group whose members they like when they have none. Attacking a member of another group
makes the two groups rivals. See `society.group_perks` for what belonging is worth.

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
| `lethal` | bool | true | false: nobody dies; whoever would die is knocked out (`knockout`): wakes up with `knockout.health`, at `knockout.place` (a hospital) if set, with `knockout.condition` if set, dropping what `drops` |
| `knockout` | `{condition, place, health, recovery}` | `{}` | See `lethal`. `recovery`: turns they then stay there recovering, out of play: they only rest (no LLM call), get +0.1 health a turn, don't hold territories, and others there see it; being healed by someone shortens it by a turn |
| `fists` | `{damage, chance}` | `{damage: 0.1, chance: 0.6}` | An attack without a weapon: health it takes and chance of landing it |
| `group_perks` | bool | true | Belonging pays: each member here adds +1 (up to +2) to rolls and defense (+5% to plain chances), members turn against whoever attacks one of them, and each group has a common stash (`give`/`take` with the group's name as target, from anywhere) |
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
- deals put into words: a `speak`/`whisper` with `item`/`amount` (what is given) and/or
  `ask_item`/`ask_amount` (what is wanted) makes the first person addressed a real offer, which they
  `accept` or `decline` like a `trade`. Words alone never move items;
- rule-driven agents (`controller: rules`, e.g. police) can't be spoken to: agents are told so;
- in the last day (or last sixth of a short run) agents are told how many turns are left and
  what still counts;
- with `rpg`:
  - attacks roll against the target's defense (10 + DEX + armor);
  - heals roll WIS (medicine);
  - `intent: persuade | deceive | intimidate` when speaking is one opposed roll against the first
    person addressed (XP only the first win of the day against each person). Winning makes them
    take the deal put into words; an intimidated person with no deal on the table runs to another
    place; a failed threat makes them angry;
  - experience and levels.

Do not recreate these as scenario actions. Add scenario actions only for things specific to the
story (bite, repair the radio, barricade the door, brew an antidote).

## Exact behaviour (answers to common questions)

- **Contagion `via: interaction`:** each time someone speaks to, whispers to, gives to, trades with,
  uses an item on, attacks, heals or does a scenario action on someone, the condition can pass in
  **both** directions with `chance`. A carrier that cannot speak still infects whoever acts on it, and
  whoever it acts on (bites, attacks).
- **Contagion `via: contact`:** only through blood and wounds: attacks, heals, items used on
  someone and scenario actions (a bite, sewing a wound, taking a blood sample). Talking, whispering,
  `interact`, giving and trading never spread it. Use it for bites; keep `interaction` for things
  in the air.
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
- **Conflict:** the decision prompt says the story is fiction and that characters may lie, steal,
  fight or kill when it fits them. Each turn agents also see who here they have reason to fight
  (enemies or rivals in relationships, members of a rival group, someone dangerous, the carrier of a
  coveted item) with their odds and the scenario actions they could use on them. Write those
  relationships, rival groups and goals in the YAML when you want conflict; small models (3B) still
  fight much less than 7B+ ones.
  When a model describes violence in free text instead of naming an action ("Golpeo a Marcos con la
  pala"), it becomes the scenario action named like the verb (`golpear`), or else the built-in
  attack, with its roll; so does a target written with extra words ("Marcos (zombi)").
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

**Crafting / recipes** (gather three radio parts and repair the radio at the tower; turn planks and
nails into a barricade):

```yaml
actions:
  reparar_radio:
    description: reparar la radio de la torre con tres juegos de piezas
    target: none
    who: {professions: [electricist], items: {piezas_radio: 3}}
    cost: {piezas_radio: 3}
    where: [Torre de radio]
    world: {radio: reparada}
  hacer_barricada:
    description: clavar tablones para hacer una barricada
    target: none
    cost: {tablones: 2, clavos: 1}
    on_self: {items: {barricada: 1}}
```

Spread the parts over places' `stock` and agents' `resources`. Agents are told what each pending
recipe still needs and where it lies, so they go and gather it.

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
