# UrjaSetu neighborhood energy workspace

A React and Three.js implementation of the UrjaSetu operations interface with a
live 3D network scene. It currently runs against deterministic demo payloads and exposes the
same transport contract intended for the Python application layer.

## Run locally

```bash
npm install
npm run dev
```

Open the URL printed by Vite. The default demo opens at 10:00 and advances one
hour every 3.5 seconds, showing solar exports and local trades immediately.
The sidebar connects Grid overview, Energy impact, Agent theatre, DISCOM ledger,
and Household. The interface adapts from desktop to mobile widths.

## Demo controls

- `1`–`5`: switch screens
- `D`: derate DT-3 and trigger a reshape block
- `C`: inject an eight-block cloud bank
- `R`: switch from mock-live data to replay mode
- `Space`: pause or resume Agent theatre

The city supports constrained orbit and zoom controls. Select a house in the
3D view, or use the keyboard-accessible node dropdown, to inspect its transformer,
phase, flow direction, PV and battery state. The Replay button also exposes the
keyboard replay action. Derate and cloud commands are disabled during replay.

## Integration boundary

Screens only use the interfaces in `src/types.ts`. Replace `DemoTransport` with
`LiveTransport` in `src/App.tsx` (`useGridTransport`) when the engine WebSocket is ready. The expected
messages are `{type: "scene"|"block"|"event", data: ...}` and match UI PRD §4.3.

Payload types are unchanged. All values remain demo data; household preferences
are local UI state. No engine or agent modules are imported. The map uses a
deterministic schematic layout, not geographic coordinates. Design tokens live
in `src/styles.css`, including the colors consumed by Three.js. There are no new
dependencies or runtime font/image requests.

## Detailed 3D neighborhood

The model includes varied multi-storey homes, terraces, balconies, solar-cell
arrays, rooftop equipment, battery indicators, EV charging canopies, landscaped
plots, streetlights, crossings, parked cars, and detailed transformer hardware.
Repeated architecture is rendered with instanced geometry grouped by material.

- Perspective / Top view: switch between the angled model and the overhead map.
- Reset camera: restore the overview after orbiting, zooming, or focusing.
- Focus home: appears after selecting a node; inspect its building up close.
- Energy flows: show or hide the largest 12 peer-to-peer trades.
- Feeder lines: show or hide routed transformer connections.

Colored plot edges reflect import/export/curtailment; battery fill reflects the
current state of charge. The architecture remains schematic. Animated trade
pulses respect reduced-motion preferences.
