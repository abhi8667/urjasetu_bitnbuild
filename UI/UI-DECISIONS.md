# UI decisions

- **Topology uses 64 nodes and four transformers.** Section 3 says three transformers and a 17/17/16 split, while sections 1, 12 and Appendix A specify 60 premises, four EV hubs and four transformers. The later locked decision wins.
- **The demo uses 24 hourly blocks per day.** The engine's locked dataset uses hourly telemetry. The interface reads this from the scene payload and remains compatible with a future 96-block scene.
- **Mock-live is the default scaffold transport.** It uses the exact transport interface and makes derate and cloud demonstrable without an engine. Pressing `R` switches to replay, where commands are disabled as required.
- **The replay fixture is generated deterministically in TypeScript.** It produces complete transport payloads and can later be replaced by `fixtures/day-one.json` without changing a screen.
- **The city uses React Three Fiber and a constrained perspective camera.** The requested 3D direction supersedes Appendix A's fixed SVG decision. Orbit, pan and zoom remain bounded so the operator cannot lose the network during a demo.
- **IBM Plex uses local fallbacks rather than a runtime font request.** A packaged webfont can be added when brand assets are finalized.
