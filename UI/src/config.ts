// Where the engine lives.
//
// This file exists because the backend URL was previously unreachable from
// configuration at all: `LiveTransport` took a URL argument and nothing ever
// constructed it, so there was no way to point the UI at a running engine
// without editing source.
//
// Set VITE_ENGINE_URL at build time (Vercel: Project Settings -> Environment
// Variables) to the Render service, e.g.
//     VITE_ENGINE_URL=https://urjasetu-engine.onrender.com
//
// Leave it unset for local development and it falls back to localhost:8000,
// which is what `uvicorn server.app:app` serves.

const RAW = (import.meta.env.VITE_ENGINE_URL ?? '').trim()

/** http(s) origin of the engine, no trailing slash. */
export const ENGINE_HTTP = (RAW || 'http://localhost:8000').replace(/\/+$/, '')

/**
 * ws(s) origin of the engine.
 *
 * The scheme has to track the HTTP one. A page served from Vercel over HTTPS
 * cannot open a plaintext `ws://` socket — browsers block mixed content with no
 * visible error beyond a console line — so an https engine URL must become wss.
 */
export const ENGINE_WS = ENGINE_HTTP.replace(/^http/, 'ws')

/**
 * Whether a live engine was configured. When VITE_ENGINE_URL is unset AND the
 * page is not being served from localhost, there is no plausible engine to
 * reach, so the UI opens on its built-in demo run rather than spending the
 * reconnect backoff failing to find one.
 */
export const HAS_CONFIGURED_ENGINE =
  Boolean(RAW) ||
  (typeof window !== 'undefined' &&
    ['localhost', '127.0.0.1'].includes(window.location.hostname))

/** Seconds of wall clock per simulated block, requested of the server. */
export const STREAM_CADENCE_S = Number(import.meta.env.VITE_STREAM_CADENCE ?? 3.5)

/** Simulated days to request. The engine caches per (days, derate). */
export const STREAM_DAYS = Number(import.meta.env.VITE_STREAM_DAYS ?? 30)

/** Block to open on. 10:00 on day one shows solar export and trading at once. */
export const STREAM_START_BLOCK = Number(import.meta.env.VITE_STREAM_START ?? 10)
