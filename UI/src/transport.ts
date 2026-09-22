import { applyDemoCommand } from './demoFixture'
import { ENGINE_WS, STREAM_CADENCE_S, STREAM_DAYS, STREAM_START_BLOCK } from './config'
import type {
  BlockPayload,
  DemoRun,
  EventPayload,
  RunSummary,
  ScenePayload,
  Transport,
  TransportStatus,
} from './types'

type Listener<T> = (value: T) => void

class Signal<T> {
  private listeners = new Set<Listener<T>>()
  on(listener: Listener<T>) { this.listeners.add(listener); return () => this.listeners.delete(listener) }
  emit(value: T) { this.listeners.forEach((listener) => listener(value)) }
}

export class ReplayTransport implements Transport {
  protected index = 0
  protected timer: number | null = null
  private eventTimers: number[] = []
  private generation = 0
  protected readonly sceneSignal = new Signal<ScenePayload>()
  protected readonly blockSignal = new Signal<BlockPayload>()
  protected readonly eventSignal = new Signal<EventPayload>()
  protected readonly statusSignal = new Signal<TransportStatus>()
  protected readonly summarySignal = new Signal<RunSummary>()

  constructor(protected readonly run: DemoRun, private readonly cadenceMs = 1800) {}

  onScene(cb: Listener<ScenePayload>) { return this.sceneSignal.on(cb) }
  onBlock(cb: Listener<BlockPayload>) { return this.blockSignal.on(cb) }
  onEvent(cb: Listener<EventPayload>) { return this.eventSignal.on(cb) }
  onStatus(cb: Listener<TransportStatus>) { return this.statusSignal.on(cb) }
  onSummary(cb: Listener<RunSummary>) { return this.summarySignal.on(cb) }
  command(_name: string, _args: Record<string, unknown>) {}

  start() {
    this.stop()
    const generation = this.generation
    queueMicrotask(() => {
      if (generation !== this.generation) return
      this.statusSignal.emit('replay')
      this.sceneSignal.emit(this.run.scene)
      this.summarySignal.emit(this.run.summary)
      this.emitCurrent()
    })
    this.timer = window.setInterval(() => {
      if (this.index >= this.run.blocks.length - 1) {
        if (this.timer !== null) window.clearInterval(this.timer)
        this.timer = null
        this.statusSignal.emit('complete')
        return
      }
      this.index += 1
      this.emitCurrent()
    }, this.cadenceMs)
  }

  stop() {
    this.generation += 1
    if (this.timer !== null) window.clearInterval(this.timer)
    this.timer = null
    this.eventTimers.forEach((timer) => window.clearTimeout(timer))
    this.eventTimers = []
  }

  seek(block: number) {
    const nextIndex = this.run.blocks.findIndex((item) => item.block === block)
    if (nextIndex < 0) throw new Error(`Block ${block} is not in this replay`)
    this.index = nextIndex
    this.statusSignal.emit('replay')
    this.emitCurrent()
  }

  protected emitCurrent() {
    this.eventTimers.forEach((timer) => window.clearTimeout(timer))
    this.eventTimers = []
    const block = this.run.blocks[this.index]
    this.blockSignal.emit(structuredClone(block))
    this.run.events.filter((event) => event.block === block.block).forEach((event, eventIndex) => {
      this.eventTimers.push(window.setTimeout(() => this.eventSignal.emit({ ...event }), eventIndex * 80))
    })
  }
}

/**
 * The offline fallback.
 *
 * This used to be the DEFAULT transport, and everything it shows is generated
 * in `demoFixture.ts` — the household bills, the DISCOM revenue, the
 * transformer life and every trade were invented in TypeScript. It is kept
 * because a demo that cannot reach its backend should still render something,
 * but the app now opens on `EngineTransport` and only falls back to this, and
 * says so in the status bar when it does.
 */
export class DemoTransport extends ReplayTransport {
  constructor(run: DemoRun) {
    super(run, 3500)
    // Open during solar generation so peer-to-peer flows are immediately visible.
    this.index = Math.max(0, run.blocks.findIndex((block) => block.clock === '10:00'))
  }

  override start() {
    super.start()
    // NOT 'live'. This is simulated demo data with no engine behind it, and
    // labelling it live is how a fixture gets mistaken for a result.
    queueMicrotask(() => this.statusSignal.emit('replay'))
  }

  override command(name: string, _args: Record<string, unknown>) {
    applyDemoCommand(this.run, this.index, name)
    this.eventSignal.emit({
      block: this.run.blocks[this.index].block,
      agent: 'operator',
      kind: 'command_received',
      text: name === 'derate' ? 'Derate queued for DT-3' : 'Cloud bank queued for eight blocks',
    })
  }

  // Inherits seek() from ReplayTransport so demo seeking works smoothly
}

/**
 * The real one: a WebSocket to the Python engine.
 *
 * `LiveTransport` (its predecessor) was written correctly and never
 * instantiated — there was no server on the other end of it, so the UI ran on
 * the fixture instead. The envelope is unchanged, because the envelope was
 * never the problem: `{type: "scene"|"block"|"event"|"summary", data: ...}`.
 *
 * Reconnect matters more here than it looks. Render's free tier spins a service
 * down after fifteen minutes idle and takes the better part of a minute to come
 * back, so the first connection after a quiet spell will fail several times
 * before it succeeds. The backoff below is capped at ten seconds and never
 * gives up, and the engine streams from a precomputed run, so reconnecting
 * resumes the walk rather than restarting the simulation.
 */
export class EngineTransport implements Transport {
  private socket: WebSocket | null = null
  private failures = 0
  private stopped = false
  private reconnectTimer: number | null = null
  private staleTimer: number | null = null
  private lastBlock = STREAM_START_BLOCK
  private totalBlocks: number | null = null
  private completed = false
  private readonly sceneSignal = new Signal<ScenePayload>()
  private readonly blockSignal = new Signal<BlockPayload>()
  private readonly eventSignal = new Signal<EventPayload>()
  private readonly statusSignal = new Signal<TransportStatus>()
  private readonly summarySignal = new Signal<RunSummary>()

  constructor(private readonly baseUrl: string = ENGINE_WS) {}

  onScene(cb: Listener<ScenePayload>) { return this.sceneSignal.on(cb) }
  onBlock(cb: Listener<BlockPayload>) { return this.blockSignal.on(cb) }
  onEvent(cb: Listener<EventPayload>) { return this.eventSignal.on(cb) }
  onStatus(cb: Listener<TransportStatus>) { return this.statusSignal.on(cb) }
  onSummary(cb: Listener<RunSummary>) { return this.summarySignal.on(cb) }

  start() { this.stopped = false; this.failures = 0; this.connect() }

  stop() {
    this.stopped = true
    this.socket?.close()
    this.socket = null
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    if (this.staleTimer !== null) window.clearTimeout(this.staleTimer)
  }

  command(name: string, args: Record<string, unknown>) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.completed = false
      this.statusSignal.emit('live')
      this.socket.send(JSON.stringify({ type: 'command', name, args }))
    }
  }

  seek(block: number) {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      throw new Error('Not connected to the engine')
    }
    this.completed = false
    this.statusSignal.emit('live')
    this.socket.send(JSON.stringify({ type: 'command', name: 'seek', args: { block } }))
  }

  private url() {
    const params = new URLSearchParams({
      days: String(STREAM_DAYS),
      cadence: String(STREAM_CADENCE_S),
      // Resume where the stream left off, so a Render cold start picks the walk
      // back up instead of jumping the viewer to midnight on day one.
      from: String(this.lastBlock),
    })
    return `${this.baseUrl}/ws?${params}`
  }

  private connect() {
    if (this.stopped) return
    this.statusSignal.emit(this.failures === 0 ? 'connecting' : 'stale')
    let socket: WebSocket
    try {
      socket = new WebSocket(this.url())
    } catch {
      this.scheduleReconnect()
      return
    }
    this.socket = socket

    socket.onopen = () => {
      this.failures = 0
      this.statusSignal.emit('live')
      this.armStaleTimer()
    }

    socket.onmessage = (message) => {
      this.armStaleTimer()
      let payload: { type?: string; data?: unknown }
      try {
        payload = JSON.parse(String(message.data))
      } catch {
        this.eventSignal.emit({
          block: this.lastBlock, agent: 'transport', kind: 'malformed_payload',
          text: 'Malformed live payload dropped',
        })
        return
      }
      if (payload.type === 'scene') {
        const scene = payload.data as ScenePayload
        this.totalBlocks = scene.total_blocks ?? null
        this.sceneSignal.emit(scene)
      }
      if (payload.type === 'summary') this.summarySignal.emit(payload.data as RunSummary)
      if (payload.type === 'block') {
        const block = payload.data as BlockPayload
        // The server keeps its shared recording available by cycling. A viewer
        // sees one finite run: once its declared final block arrives, hold that
        // result until the user explicitly seeks or restarts.
        if (this.completed) return
        this.lastBlock = block.block
        this.blockSignal.emit(block)
        if (this.totalBlocks != null && block.block >= this.totalBlocks - 1) {
          this.completed = true
          this.statusSignal.emit('complete')
        }
      }
      if (payload.type === 'event') {
        const event = payload.data as EventPayload
        if (!this.completed || event.block === this.lastBlock) this.eventSignal.emit(event)
      }
    }

    socket.onerror = () => { /* onclose follows; handled there */ }
    socket.onclose = () => {
      if (this.stopped) return
      this.socket = null
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect() {
    this.failures += 1
    this.statusSignal.emit(this.failures >= 3 ? 'disconnected' : 'stale')
    // Capped at 10s. A Render cold start is roughly a minute, so this retries
    // about ten times across it and then keeps trying — it never gives up,
    // because "gave up at second 40 of a 60-second cold start" is the failure
    // mode that makes a deployed demo look broken when it is merely asleep.
    const delay = Math.min(10_000, 500 * 2 ** Math.min(this.failures - 1, 5))
    this.reconnectTimer = window.setTimeout(() => this.connect(), delay)
  }

  private armStaleTimer() {
    if (this.staleTimer !== null) window.clearTimeout(this.staleTimer)
    // Generous relative to the block cadence: at 3.5s per block a 5s timeout
    // (the old value) flickered 'stale' between every perfectly healthy block.
    const idleMs = Math.max(12_000, STREAM_CADENCE_S * 1000 * 3)
    this.staleTimer = window.setTimeout(() => this.statusSignal.emit('stale'), idleMs)
  }
}

/** Kept as an alias so any older import still resolves. */
export const LiveTransport = EngineTransport
