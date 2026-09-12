# UrjaSetu — Interface PRD

**Version** 1.0
**Audience** An AI coding agent, plus 2–3 human reviewers.
**Scope of this document** The front end only. The engine and its data sources are specified elsewhere.

---

## 0. How to use this document

Build screen by screen in the order given in §12. Each stage has a checkpoint; do not proceed past a failing one.

Rules for the executing agent:

1. The front end never imports from, reads, or reasons about engine internals. It consumes the transport interface in §4 and nothing else.
2. Everything renders correctly from replay. If a feature only works with a live engine, it is built wrong.
3. No feature may block on a network message. Every panel has a defined empty and stale state (§11).
4. Design tokens in §3 are the only source of colour, type, and spacing. No literal hex values in component code.
5. Record any ambiguity resolved during implementation in `UI-DECISIONS.md`.

---

## 1. Scope

### In scope

- Isometric 2.5D city visualisation of 50 houses and 3 transformers
- Five screens (§8)
- Transport layer with live and replay adapters behind one interface
- Two live demo controls: transformer derate, cloud bank
- Motion system including house-to-house trade arcs
- Keyboard-driven navigation

### Out of scope

- Engine internals, agent logic, market clearing, constraint solving
- Data acquisition, cleaning, or the meter feed
- Authentication, onboarding, settings pages, persistence of UI state
- Mobile layout. Target is a projector at 1920×1080. Degrade gracefully below 1280px but do not optimise for it.

---

## 2. Design direction

### 2.1 Subject and audience

This is an operations instrument for an electricity distribution network, watched by grid operators and, during the demo, by judges standing three metres from a projector. Its job is to make an invisible negotiation legible: power moving between houses, and a transformer quietly being spared.

The visual vernacular is **instrumentation**, not dashboard. The reference points are substation control panels, electrical single-line diagrams, and the actual streetscape of an Indian residential colony at the two hours that matter — the noon glut and the evening peak.

### 2.2 Tokens

Colour is functional throughout. Nothing is tinted for decoration.

```
--ground        #16202B    deep slate-blue, the base plane
--ground-raised #1E2A38    panel surfaces
--rule          #2C3A4A    hairlines, wire casings
--ink           #E8EDF2    primary text
--ink-quiet     #8FA0B2    secondary text and labels

--export        #E9B44C    solar amber — a house sending power out
--import        #4FA8C5    cool cyan — a house drawing power in
--curtailed     #C4724A    clay — a trade the flow agent trimmed
--idle          #5A6978    slate — neither exporting nor importing
--stress        #D64F4F    a transformer past its limit
```

The amber/cyan pair carries the entire story: warm is generation, cool is consumption. A judge learns it in two seconds without reading a legend, and it holds at projector distance.

Backgrounds use a real hue rather than a tinted black. The base plane is blue because the scene is read at dusk, when the evening peak happens.

### 2.3 Type

**IBM Plex Sans** for everything, **IBM Plex Mono** for the trace panel and for tabular figures only.

Plex is chosen for its instrumentation heritage rather than as a neutral default; its numerals are unambiguous at distance, which matters when a judge is reading a clearing price from across a room. Mono appears only where the content genuinely is machine output — the trace log and numeric columns — never as styling for labels.

Scale, 1.25 ratio from a 16px base:

```
display   39px / 1.1   600    block clock, headline figures
title     25px / 1.2   500    screen titles
body      16px / 1.5   400    prose
label     13px / 1.4   500    panel labels, axis ticks
trace     13px / 1.5   400    mono, the agent log
```

Sentence case throughout. No all-caps labels. No eyebrow text above headings.

### 2.4 Layout

The city is the hero and is full-bleed. Panels dock to it rather than sitting beside it as equal cards.

```
┌─────────────────────────────────────────────┬──────────────┐
│                                             │              │
│                                             │   trace      │
│            isometric city                   │   (mono,     │
│            (full bleed)                     │   scrolling) │
│                                             │              │
│                                             │              │
├──────────────┬──────────────┬───────────────┤              │
│ block clock  │ clear price  │ worst loading │              │
└──────────────┴──────────────┴───────────────┴──────────────┘
```

Metrics sit in a bottom strip as a single horizontal rule of figures, not as three rounded cards. Cards are used only where content genuinely is a repeated list of like items — the transformer fleet table and nothing else.

### 2.5 Motion principles

One orchestrated moment per block: the arcs fire, the colours settle, the trace lines append. That is the page's only ambient motion.

No hover transitions on panels. No entrance animations on screen change. Motion exists to show that something moved — energy along a wire, a trade being trimmed — and nowhere else.

`prefers-reduced-motion` disables arcs and pulses; colour state changes remain, applied instantly.

---

## 3. Reference scene

```
houses:        50
transformers:  3
cluster split: 17 / 17 / 16
blocks/day:    96
replay rate:   60× (one block = 15s wall clock)
```

The front end reads these from the scene payload (§4.3) and must not hardcode them. A run with 30 houses and 1 transformer must render without code changes.

---

## 4. Transport

### 4.1 Interface

Both adapters satisfy one interface. Screens depend only on this.

```js
interface Transport {
  onScene(cb)        // fires once: topology and static config
  onBlock(cb)        // fires per block: full state snapshot
  onEvent(cb)        // fires per agent event, ordered within a block
  onStatus(cb)       // 'live' | 'replay' | 'stale' | 'disconnected'
  command(name, args) // demo controls; no-op in replay
  seek(block)        // replay only; throws in live
}
```

### 4.2 Adapters

**LiveTransport.** WebSocket. Reconnects with exponential backoff to 5s, capped. On three consecutive failures it emits `disconnected` and the shell offers a one-key switch to replay.

**ReplayTransport.** Reads a completed run's exported JSON, emits blocks on a timer at the same cadence. Supports `seek`. Every screen must be fully functional under replay — this is the fallback if the engine dies during the pitch.

**Invariant T1.** A screen that renders correctly under `ReplayTransport` renders correctly under `LiveTransport`. Screens contain no adapter-specific branches.

### 4.3 Payload shapes

`scene`, once:

```json
{
  "houses": [{"id":"H-07","transformer":"T-1","phase":"A",
              "x":3,"y":1,"has_pv":true,"has_battery":true}],
  "transformers": [{"id":"T-1","rating_kva":100,"x":0,"y":0}],
  "blocks_per_day": 96,
  "replay_rate": 60
}
```

`block`, per block:

```json
{
  "block": 842,
  "clock": "14:15",
  "day": 9,
  "clearing_price": 6.20,
  "status": "reshaped",
  "houses": {"H-07": {"net_kwh": 1.4, "state": "export",
                      "soc_frac": 0.62, "curtailed": 0.12}},
  "transformers": {"T-1": {"loading": 0.94, "hotspot_c": 98.2,
                           "life_used_frac": 0.031, "stressed": false}},
  "trades": [{"from":"H-07","to":"H-22","kwh":1.4,"price":6.20,
              "curtailed":0.12}]
}
```

`event`, zero or more per block:

```json
{"block": 842, "agent": "sentinel", "kind": "breach_detected",
 "text": "T-3 projected 112% at 14:15"}
```

**Invariant T2.** `block.status` is one of `cleared`, `reshaped`, `fallback`. The city's status badge renders directly from this field and never infers it.

---

## 5. Isometric projection

True isometric, 30° axes.

```js
const S = 34;              // tile size in px, tuned to fit 50 houses at 1920px
const COS30 = Math.cos(Math.PI / 6);
const SIN30 = 0.5;

function project(gx, gy, gz = 0) {
  return {
    x: (gx - gy) * COS30 * S,
    y: (gx + gy) * SIN30 * S - gz * S
  };
}
```

**Draw order.** Painter's algorithm: sort all drawables by `gx + gy` ascending, then by `gz` ascending. Wires draw before the buildings they connect so poles occlude them correctly. Trade arcs draw last, above everything.

**No camera.** Fixed projection, no pan, no zoom, no rotation. The viewBox is computed once from scene bounds with a 2-tile margin. This removes an entire class of live-demo accidents.

---

## 6. Layout algorithm

Houses cluster around their transformer; they are not scattered.

1. Place transformers along the x axis at even spacing, computed from cluster sizes.
2. For each transformer, lay its houses in two rows facing each other across a street running parallel to the y axis — the arrangement of an actual residential colony.
3. Within a cluster, order houses by `phase` so phase grouping is visible as a pattern, then by id for determinism.
4. Reserve one tile between clusters as a gap.

**Invariant L1.** Layout is a pure function of the scene payload. The same scene produces identical coordinates on every run.

**Invariant L2.** No two drawables occupy the same grid cell.

---

## 7. Visual encoding

| Thing | Channel | Encoding |
|---|---|---|
| House, exporting | roof fill | `--export`, opacity scaled 0.4–1.0 by magnitude |
| House, importing | roof fill | `--import`, same scaling |
| House, idle | roof fill | `--idle` |
| House, curtailed this block | roof outline | 2px `--curtailed` stroke |
| House with battery | small block beside house | fill height = state of charge |
| House with PV | roof panel glyph | present or absent |
| Transformer, normal | box fill | `--ground-raised` with `--rule` outline |
| Transformer, loading | ring around box | arc sweep 0–360° = 0–100% loading |
| Transformer, over limit | ring + box | `--stress`, slow pulse |
| Wire | line, transformer to house | `--rule` when idle |
| Wire, carrying power | line | tinted toward `--export` or `--import` by direction |
| Phase | house base plinth | three subtle tones of `--rule` |

Magnitude maps to **opacity and stroke, never to size.** House footprints stay constant so the city does not visually breathe, which is distracting at replay speed.

---

## 8. Motion specification

### 8.1 Trade arcs

The signature element. One arc per trade, seller to buyer.

- Quadratic Bézier from seller roof to buyer roof, control point lifted `0.3 × distance` above the midpoint in screen space.
- A travelling dot moves seller → buyer over 900ms with `cubic-bezier(0.4, 0, 0.2, 1)`.
- Arc stroke is `--export` fading to `--import` along its length, 1.5px, 0.35 opacity.
- A curtailed trade draws its arc dashed and in `--curtailed`.

**Cap: 12 concurrent arcs per block**, selected as the 12 largest by kWh. Remaining trades are summarised as a count in the bottom strip. At 50 houses, drawing every arc produces unreadable spaghetti; this is a hard limit, not a performance tuning value.

Arcs stagger by 40ms in descending size order so the largest trades read first.

### 8.2 Wire pulse

Wires carrying power show a slow travelling gradient, 2s loop, direction matching power flow. Amplitude scales with magnitude. This is ambient and must never compete with the arcs for attention.

### 8.3 Reshape moment

When `status` transitions to `reshaped`, one orchestrated sequence, 1.2s total:

1. The breaching transformer's ring flashes `--stress` (150ms)
2. Affected houses gain their curtailed outline (staggered, 300ms)
3. Battery blocks on absorbing houses grow to their new level (400ms)
4. The status badge changes to `Reshaped`
5. The trace panel appends the sentinel and flow lines

This is the demo's climax and is the only place in the interface where sequencing is choreographed rather than simultaneous.

### 8.4 Budgets

- 60fps sustained at 50 houses with 12 arcs
- Arc animation via CSS transforms only; no per-frame JS layout
- One `requestAnimationFrame` loop for the whole scene, not one per element
- Full block render under 16ms, measured by acceptance check §13.6

---

## 9. Screens

Navigation is keyboard-first: keys `1`–`5` switch screens instantly, no transition. The shell renders the active screen and nothing else.

### 9.1 City — key `1`

The hero. Full-bleed isometric scene, docked trace rail, bottom metric strip.

Components:
- `IsoCity` — the scene, §5–8
- `TracePanel` — mono, newest at bottom, auto-scroll, agent name prefixing every line, 40 lines retained
- `MetricStrip` — block clock and day, clearing price, worst transformer loading, trade count with capped-arc note
- `StatusBadge` — `Cleared` / `Reshaped` / `Fallback`, from `block.status`
- `ControlBar` — the two demo commands (§10)

### 9.2 Compare — key `2`

Three paired rows. Net metering left, UrjaSetu right, delta centre. No chart, no chrome, no card borders.

```
                  Net metering    UrjaSetu      Delta
Household bill      ₹1,840         ₹1,612       −₹228
DISCOM revenue        ₹0           ₹4,310      +₹4,310
Transformer life    0.061%         0.043%       −29%
```

Figures in Plex Mono, tabular numerals, right-aligned. Delta is the only coloured column: `--export` for favourable, `--stress` for unfavourable.

Below the table, one line of plain text stating the run parameters — days simulated, houses, transformers — so a judge knows what they are looking at without asking.

### 9.3 Agent theatre — key `3`

The architecture explainer. One agent lit at a time, stepping through a block's causal chain.

Components:
- `AgentGrid` — seven chips, active one raised and outlined
- `PhaseLabel` — gathering bids / clearing / constraint check / reshaping / re-clearing / settling
- `MessagePanel` — the current agent's terse output, mono
- `StepProgress` — position within the block's sequence
- `PauseButton` — pause on the current step

Sequential, never parallel. The market agent appears twice in the sequence, which is how the re-clear loop becomes visible without drawing an arrow.

Driven by the same `onEvent` stream as the trace panel, rendered slower.

### 9.4 DISCOM ledger — key `4`

Components:
- `RevenueFigure` — wheeling plus transaction charges collected, cumulative
- `FleetTable` — transformers sorted worst-first: id, loading, hot-spot, life used, risk rank. The only place cards or table chrome appear.
- `DeferredCapex` — estimated replacement deferred, in rupees
- `CrossSubsidyToggle` — flips the surcharge on; the revenue figure and delta update live

### 9.5 Household — key `5`

Deliberately small. Five elements, one screen, no scrolling.

- Today's earnings or savings, as a display figure
- Battery state, as a single bar
- Aggression dial, three positions
- Spend cap, as a progress bar against the monthly ceiling
- One line: savings versus DISCOM-only this month

Selects a house via a dropdown defaulting to the most active prosumer in the current block.

---

## 10. Demo controls

Two commands, both in `ControlBar` on the city screen, both also bound to keys.

| Control | Key | Command | Expected visible effect |
|---|---|---|---|
| Derate transformer | `d` | `command('derate', {transformer_id, factor: 0.6})` | Ring goes `--stress`, reshape sequence fires within one block |
| Cloud bank | `c` | `command('cloud', {cover: 0.8, blocks: 8})` | Export colours fade across the scene over the following blocks, clearing price rises |

In replay mode both are disabled and the buttons show a `Replay` state rather than disappearing — a control that vanishes mid-demo is more confusing than one that is visibly inert.

**Invariant C1.** A control press that produces no visible change within two blocks logs a warning to the trace panel. Silent failure during a demo is the worst outcome.

---

## 11. Empty, stale and failure states

| Condition | Behaviour |
|---|---|
| Before first block | City renders in full `--idle`; strip shows `Waiting for first block` |
| No trades this block | City holds previous colours; strip shows `No trades cleared`; no arcs |
| Transport stale > 5s | Thin `--stress` rule across the top of the shell; last known state stays rendered |
| Transport disconnected | Same rule, plus a line offering key `r` to switch to replay |
| Malformed payload | Drop that block, log to trace, keep rendering the previous state. Never blank the screen. |
| Missing house in payload | Render as idle. Do not remove it from the scene. |

The interface never shows a blank screen or a spinner after first paint. A stale city is more useful to an operator, and more defensible on stage, than an empty one.

---

## 12. Build order

| Stage | Work | Checkpoint |
|---|---|---|
| 1 | Shell, tokens, keyboard routing, `ReplayTransport` against a fixture file | Keys 1–5 switch between five placeholder screens |
| 2 | Projection, layout algorithm, static city render | 50 houses and 3 transformers render in correct isometric order from a fixture scene |
| 3 | Block subscription, house and transformer colour encoding | Scene recolours correctly stepping through 96 fixture blocks |
| 4 | `TracePanel`, `MetricStrip`, `StatusBadge` | City screen complete except motion |
| 5 | Wire pulses and trade arcs with the 12-arc cap | Arcs render at 60fps across a full fixture day |
| 6 | Reshape choreography | Injecting a fixture reshape block fires the full 1.2s sequence |
| 7 | `LiveTransport` and status handling | Same screens work against a running engine; killing the engine degrades to stale, not blank |
| 8 | Compare screen | Renders from a fixture run summary |
| 9 | Demo controls | Derate and cloud produce visible effects within two blocks |
| 10 | Agent theatre | Steps through a block's events with pause |
| 11 | DISCOM ledger | Fleet table sorts and cross-subsidy toggle updates live |
| 12 | Household screen | Five elements, house selector works |

**Stages 1–7 are the demonstrable interface.** Stage 9 is the highest-value remaining item after that — prioritise the controls over the three secondary screens if time is short.

---

## 13. Acceptance checks

1. **Replay parity.** Every screen renders identically under `ReplayTransport` and `LiveTransport` given the same run.
2. **Determinism of layout.** The same scene payload produces pixel-identical city geometry across reloads.
3. **Topology independence.** Renders correctly with 30 houses / 1 transformer and 50 houses / 3 transformers, no code changes.
4. **Arc cap.** A block with 30 trades renders exactly 12 arcs and reports the remainder in the strip.
5. **Reshape choreography.** The full sequence fires, in order, within 1.2s of a `reshaped` block arriving.
6. **Frame budget.** 60fps sustained through a full simulated day at the reference scene; no block render exceeds 16ms.
7. **Degradation.** Killing the transport mid-run leaves the last state rendered with a visible stale indicator, and `r` switches to replay without a reload.
8. **Malformed input.** A block payload with a missing house, a null price, and an unknown status renders without throwing.
9. **Reduced motion.** With `prefers-reduced-motion`, arcs and pulses are absent and all state changes remain legible.
10. **Control feedback.** Derate and cloud each produce a visible change within two blocks, or a warning appears in the trace.

---

## 14. Definition of done

- All §13 checks pass
- Every screen reachable by keyboard alone
- No hex literal outside the token definitions
- No screen imports anything from the engine package
- A full demo run completes from replay with the engine process not running at all
- `UI-DECISIONS.md` records every ambiguity resolved

---

## Appendix A — Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Data source | Live with replay fallback | Live is the honest claim; replay is the insurance |
| Rendering | Isometric 2.5D SVG | Most of the visual payoff of 3D at a quarter of the build cost, and nothing to fumble on a projector |
| Camera | Fixed, none | Removes a class of live-demo accidents |
| Scene | 50 houses, 3 transformers | Enough density to look like a colony; enough transformers for the ageing signal to route around one |
| Arcs | House-to-house, capped at 12 | Below the cap it reads as trading; above it reads as noise |
| Screens | Five | City and compare carry the demo; the other three answer predictable questions |
| Controls | Derate and cloud | One tests the fast loop, one tests forecasting; both are visible within two blocks |
| Magnitude encoding | Opacity and stroke, not size | A city that visually breathes is distracting at 60× replay |
| Framework | None; SVG and vanilla JS | A build step is a liability at hour 32 |
