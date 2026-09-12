# UrjaSetu AI and ML agents

The server simulation connects the model pipeline before orders are built:

1. `GridFailureRiskAgent` fits logistic regression on the project's simulated meter feed. Each block it predicts overload risk for every transformer.
2. `AITradingStrategyAgent` receives those risk scores, weather, prior strategy, and market price history once per simulated day. It calls Groq and clamps the returned discount and margin before distributing them to all consumer and prosumer agents.
3. Market clearing, grid constraints, batteries, and settlement continue to enforce the physical and accounting rules.

`server/simulation.py` wires both agents into `Runner`. The server precomputes a run and then streams its recorded decisions. The network tab mirrors the source city tab's playback through a session-specific BroadcastChannel.

## Configuration

ML is enabled by `risk_enabled` in the simulation configuration (default true). Its training source is synthetic project telemetry; risk scores are not validated field forecasts.

For Groq, the backend reads `GROQ_API_KEY`, `URJASETU_LLM_ENABLED=true`, `GROQ_MODEL`, and `GROQ_TIMEOUT_SECONDS`. The fallback model comes from `groq_fallback_model` in the simulation configuration. Environment settings load at process startup; restart the backend to apply changes or rebuild cached runs.

The strategy agent attempts the primary model and then the fallback. If both fail, it keeps the previous strategy. Disabled configuration, provider failure, a new decision, and reuse of the daily decision are explicitly reported. A configured key alone does not prove a successful provider call.

## Events

- `grid_risk_predicted`: per-transformer probability and prediction horizon, every block.
- `ai_strategy_updated`: daily decision attempt with success, disabled, or fallback state.
- `ai_strategy_status`: strategy reuse or continuing fallback/disabled state for subsequent blocks.

The frontend identifies these as `grid_risk` (ML) and `ai_trading` (LLM). Communication links remain documented workflow paths; the underlying event bus does not record explicit recipient acknowledgements.

## Verification

Run `python tests/test_ai_agents.py`, `python -m unittest discover -s tests -p test_strategy_stream.py`, and `npm --prefix UI run build`.

Provider substitutes cover risk propagation, strategy distribution, disabled configuration, fallback, and event forwarding without transmitting project data. A real Groq verification sends risk and market context externally and requires network access.
