# UrjaSetu interface scaffold

A React and Three.js implementation of the UrjaSetu operations interface with a
live 3D network scene. It currently runs against deterministic demo payloads and exposes the
same transport contract intended for the Python application layer.

## Run locally

```bash
npm install
npm run dev
```

Open the URL printed by Vite. The default mock-live run advances automatically.

## Demo controls

- `1`–`5`: switch screens
- `D`: derate DT-3 and trigger a reshape block
- `C`: inject an eight-block cloud bank
- `R`: switch from mock-live data to replay mode
- `Space`: pause or resume Agent theatre

The city supports constrained orbit, pan and zoom controls. Select a house to
inspect its transformer, phase, flow direction, PV and battery state.

## Integration boundary

Screens only use the interfaces in `src/types.ts`. Replace `DemoTransport` with
`LiveTransport` in `src/main.ts` when the engine WebSocket is ready. The expected
messages are `{type: "scene"|"block"|"event", data: ...}` and match UI PRD §4.3.
