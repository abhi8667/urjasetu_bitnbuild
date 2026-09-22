# UI decisions

- **Topology uses 64 nodes and four transformers.** Section 3 says three transformers and a 17/17/16 split, while sections 1, 12 and Appendix A specify 60 premises, four EV hubs and four transformers. The later locked decision wins.
- **The demo uses 24 hourly blocks per day.** The engine's locked dataset uses hourly telemetry. The interface reads this from the scene payload and remains compatible with a future 96-block scene.
- **Mock-live is the default scaffold transport.** It uses the exact transport interface and makes derate and cloud demonstrable without an engine. Pressing `R` switches to replay, where commands are disabled as required.
- **The replay fixture is generated deterministically in TypeScript.** It produces complete transport payloads and can later be replaced by `fixtures/day-one.json` without changing a screen.
- **The city uses React Three Fiber and a constrained perspective camera.** The requested 3D direction supersedes Appendix A's fixed SVG decision. Orbit, pan and zoom remain bounded so the operator cannot lose the network during a demo.
- **IBM Plex uses local fallbacks rather than a runtime font request.** A packaged webfont can be added when brand assets are finalized.
# Visual redesign, September 2026

The user's creative redesign request supersedes the original dark palette and
linear street arrangement. Work stays entirely in UI/.

- Palette: mist #f2f6f5, white #ffffff, forest #153e36, teal #268e82,
  solar amber #d99b33, warning coral #cf5b51. Export/import colors still encode
  energy direction; red communicates capacity breaches.
- Typography: Aptos with Segoe UI and sans-serif fallbacks; a compact humanist
  interface with tabular figures. No external font dependency.
- Layout: persistent forest sidebar, quiet workspace header, block metrics,
  neighborhood model with adjacent activity, then transformer capacity cards.
  The model is the main visual feature; supporting views share the same shell.
- Review against the brief: retain the 3D network and amber/teal energy encoding
  rather than introduce an unrelated hero illustration. Replace the long row
  of houses with four neighborhoods so 64 nodes are legible together. Use a
  schematic, deterministic layout and explicitly identify it as a model.
- Model: four compact transformer clusters, streets, rooftop PV, battery meters,
  state-colored roofs, transformer labels and animated trade paths. Camera fits
  narrow viewports; panning is disabled to keep the network in reach.
- Interaction: preserve all five views and keyboard shortcuts, expose replay as
  a button, and add a native node selector alongside direct 3D selection.
- Demo: open at 10:00, advance hourly blocks every 3.5 seconds. Replay retains
  its original cadence and begins at block zero. Clear delayed event timers
  and old subscriptions when changing transports.
- Integration: payload interfaces and engine files are untouched. Demo, replay,
  and WebSocket adapters retain the Transport interface. Household buying
  preferences remain local preview state. No new dependencies were needed.
- Verification: production TypeScript/Vite build; headless Edge interaction
  checks for all five screens, household controls, node inspection, derating,
  cloud acknowledgement, replay lockout, and 768px/390px layouts. Desktop and
  mobile screenshots reviewed. Vite retains its existing large Three.js bundle
  warning; code splitting remains a future integration optimization.

## Detailed 3D revision

The follow-up request prioritizes the model itself. Replace basic primitives
with a warm architectural miniature, contrasting blue solar cells and glazing,
green planting, neutral walls, and amber trade paths. Increase the model viewport
and use an orthographic camera fitted to projected bounds for reliable framing.

Architecture is instanced by material and shape; selection maps instance IDs
back to payload node IDs. Transformer hardware remains individually rendered.
House variation is deterministic presentation data, not a claim about real
building heights. Keep import/export edges, dynamic battery fill, stressed
transformers, and the 12-trade cap. Feeder lines are optional to reduce visual
clutter. Add overhead view, reset, focus selection, and independent layer toggles.

Verified the new view/layer/focus/reset controls and reduced-motion behavior in
headless Edge, alongside the existing UI checks. Reviewed overview, mobile,
overhead, and close-up screenshots. No engine or payload changes.

## Progressive simulation integrity, September 2026

- The streamed recording is presented as a finite simulation, never as present-day
  field telemetry. The UI labels the playhead as simulated time and shows progress
  against the run's declared total block count.
- Telemetry contains observed blocks only. Seeking backwards removes later blocks
  from the chart and event trace, so cached observations cannot look like a forecast.
- Full-run economics, governance findings, and briefings remain hidden until the
  final block. The server may retain and cycle its shared recording, but the client
  holds the final state until an explicit seek or restart.
- Battery fill and movement come only from `BatteryBook` snapshots. Per-premises
  charged/discharged kWh travels in each block payload; the UI does not interpolate
  state-of-charge, force exporters to 100%, or infer custody from ordinary trades.
- Structural emoji have been removed in favor of text and the existing SVG icon
  language.
