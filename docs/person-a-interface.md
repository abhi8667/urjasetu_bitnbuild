# Person A — Interface

**Mission** The isometric city and five screens. You turn an invisible negotiation into something a judge understands from three metres away.

**You are the least blocked person on the team and the most visible.** From hour 2 you work against a fixture file and need nothing from anyone for the next twenty hours. Use that.

**Your full specification is `urjasetu-ui-prd.md`.** This document is the implementation plan, not the spec — read that one first, refer back to it constantly.

---

## 1. What you own

```
ui/shell/          routing, tokens, layout frame
ui/city/           projection, layout, rendering, motion
ui/screens/        the five screens
ui/transport/      live and replay adapters
fixtures/          shared, maintained by you
```

## 2. What you never touch

Any engine module. You consume the transport interface and nothing else. If you find yourself importing from `agents/`, `grid/`, or `algo/`, stop.

---

## 3. Hour 0–2: the handshake that unblocks you

Sit with B for 30 minutes. Freeze three payload shapes exactly as in UI PRD §4.3: `scene`, `block`, `event`.

Then write `fixtures/day-one.json` together — one simulated day, 96 blocks, hand-made or scripted. Include, deliberately:

- a normal block with 8 trades
- a block with 30 trades, to prove the 12-arc cap
- a `reshaped` block with curtailed houses and battery absorption
- a `fallback` block
- a block with no trades at all
- a malformed block, to prove §11 of the spec

You now have twenty hours of unblocked work. **Do not wait for a running engine at any point before hour 24.**

---

## 4. Implementation order

| Order | Work | Target hour | Gate |
|---|---|---|---|
| 1 | Shell, tokens, keyboard routing | 4 | Keys 1–5 switch five placeholder screens |
| 2 | Projection and layout | 8 | 50 houses, 3 transformers, correct draw order |
| 3 | Block subscription, colour encoding | 12 | Scene recolours across 96 fixture blocks |
| 4 | Trace panel, metric strip, status badge | 16 | City screen complete except motion |
| 5 | Wire pulses, trade arcs, 12-cap | 20 | 60fps across a full fixture day |
| 6 | Reshape choreography | 24 | The 1.2s sequence fires on a `reshaped` block |
| 7 | LiveTransport, degradation states | 26 | **Integration: real city from real run** |
| 8 | Compare screen | 28 | Real numbers from a run summary |
| 9 | Demo controls | 30 | Derate and cloud visibly work |
| 10 | Theatre, ledger, household | 30+ | Cuttable, in that order |

**Stages 1–7 are the demonstrable interface.** Stage 9 is the highest-value remaining item after that. The last three screens are the first three cuts.

---

## 5. Implementation notes

### 5.1 Projection — hour 8

```js
const S = 34;                         // tile px, tuned for 50 houses at 1920
const COS30 = Math.cos(Math.PI / 6);

function project(gx, gy, gz = 0) {
  return { x: (gx - gy) * COS30 * S,
           y: (gx + gy) * 0.5 * S - gz * S };
}
```

**Draw order is painter's algorithm:** sort all drawables by `gx + gy`, then `gz`. Wires before buildings so poles occlude correctly. Arcs last, above everything.

**No camera.** Fixed viewBox computed once from scene bounds plus a 2-tile margin. No pan, no zoom, no rotate. This removes an entire class of live-demo accidents and saves you three hours.

### 5.2 Layout — hour 8

Houses cluster around their transformer; they are not scattered.

1. Transformers evenly spaced along x, from cluster sizes
2. Per cluster, two rows of houses facing each other across a street on the y axis
3. Within a cluster, order by phase then id
4. One tile gap between clusters

**Invariant L1.** Layout is a pure function of the scene payload. Same scene, same pixels, every reload.

### 5.3 Colour encoding — hour 12

Amber out, cyan in. This carries the entire story and needs no legend.

```
--export    #E9B44C    exporting
--import    #4FA8C5    importing
--curtailed #C4724A    trimmed this block, as a 2px outline
--idle      #5A6978    neither
--stress    #D64F4F    transformer over limit
```

**Magnitude maps to opacity, 0.4–1.0. Never to size.** A city that visually breathes at 60× replay is distracting and hides the thing you want seen.

### 5.4 Trade arcs — hour 20. Your signature element.

Quadratic Bézier, seller roof to buyer roof, control point lifted `0.3 × distance` above the midpoint. A travelling dot over 900ms, `cubic-bezier(0.4, 0, 0.2, 1)`. Stroke fades `--export` → `--import` along its length, 1.5px, 0.35 opacity. Curtailed trades draw dashed in `--curtailed`.

**Cap at 12 arcs, the largest by kWh.** Stagger by 40ms descending. Remaining trades become a count in the strip.

This is a hard limit. At 50 houses you will have 20–30 trades a block and drawing them all is unreadable. Someone will ask you to raise it once it works; do not.

**Performance:** CSS transforms only. One `requestAnimationFrame` loop for the whole scene, not one per element. Budget: full block render under 16ms.

### 5.5 Reshape choreography — hour 24. The climax.

On `status` transitioning to `reshaped`, one sequence, 1.2s:

1. Breaching transformer ring flashes `--stress` — 150ms
2. Affected houses gain curtailed outlines, staggered — 300ms
3. Battery blocks grow to new levels — 400ms
4. Status badge changes to `Reshaped`
5. Trace appends the sentinel and flow lines

This is the only choreographed sequence in the interface. Everywhere else, state changes are simultaneous.

### 5.6 Transports — hour 26

Both satisfy one interface. **No screen contains an adapter-specific branch.**

`ReplayTransport` reads an exported run and emits on a timer. `LiveTransport` is a WebSocket with backoff to 5s, and after three failures emits `disconnected` and offers key `r` to switch to replay.

**Invariant T1.** A screen that works under replay works under live. Test replay first, always.

---

## 6. Your hour-by-hour

| Hours | Work | Done when |
|---|---|---|
| 0–2 | Handshake with B, fixture file | Six fixture cases written |
| 2–4 | Shell, tokens, routing | Keys 1–5 work |
| 4–8 | Projection, layout, static city | 50 houses render correctly |
| 8–12 | Block subscription, colours | Recolours across 96 blocks |
| 12–16 | Trace, strip, badge | City screen complete, still |
| 16–20 | Wire pulses, arcs, cap | 60fps for a full day |
| 20–24 | Reshape choreography | Sequence fires correctly |
| 24–26 | LiveTransport, **integration** | Real city, real run |
| 26–28 | Compare screen | Real numbers |
| 28–30 | Demo controls | `d` and `c` visibly work |
| 30–33 | Fixture refresh, triage, **demo script** | — |
| 33–36 | Rehearsal ×4 | — |

---

## 7. Empty, stale and failure states — build these, do not defer them

| Condition | Behaviour |
|---|---|
| Before first block | Full `--idle` city, strip reads `Waiting for first block` |
| No trades this block | Hold previous colours, `No trades cleared`, no arcs |
| Stale over 5s | Thin `--stress` rule across the top, last state stays rendered |
| Disconnected | Same rule plus a line offering key `r` |
| Malformed payload | Drop that block, log to trace, keep the previous state |
| Missing house | Render idle. Do not remove it from the scene. |

**Never a blank screen or a spinner after first paint.** A stale city is more defensible on stage than an empty one, and this is the code that saves you if the engine dies mid-pitch.

---

## 8. Fallbacks

| If | Then |
|---|---|
| Arcs won't hit 60fps | Drop to 8 arcs, then to a single pulse along the wire |
| Isometric layout fights you past hour 10 | Fall back to a flat top-down grid. Less pretty, same information. |
| Choreography is fiddly | Fire all five steps simultaneously. Still readable, less dramatic. |
| LiveTransport unstable at hour 28 | Ship replay-only and say so. Nobody can tell from the audience. |
| Behind at hour 30 | Cut household, then ledger, then theatre. City plus compare is a complete demo. |

---

## 9. Your second job: the demo script

You draft it at hour 24, everyone edits. Roughly three and a half minutes:

| Time | Screen | What is said |
|---|---|---|
| 0:00–0:30 | Compare, static | The problem — net metering prices energy but not time, place, or wear |
| 0:30–1:30 | Theatre | The architecture, one agent at a time |
| 1:30–3:00 | City, live | Let it run. Press `d`. Watch the reshape. |
| 3:00–3:30 | Compare, live | The payoff — three paired numbers |
| On request | Ledger | Why the DISCOM wants this |

**Everyone must be able to drive it.** You will be answering questions about what is on screen; someone else will have the keyboard. Teach them the keys at hour 30, not hour 35.

**Build a manual step mode behind a keypress.** If the sim crashes mid-pitch, you step the sequence by hand and nobody in the audience knows. That fallback has saved more demos than testing has.

---

## 10. What you present

**Questions you own**
- Anything visible on screen
- "Is this live or a video?" — press `d` and let the reshape answer
- What the colours mean
- Why 50 houses and 3 transformers

You are also the person most likely to be asked to run it again. Make sure a fresh run takes under thirty seconds to start.
