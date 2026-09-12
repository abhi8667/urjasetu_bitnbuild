import { applyDemoCommand } from './demoFixture'
import type { BlockPayload, DemoRun, EventPayload, ScenePayload, Transport, TransportStatus } from './types'

type Listener<T> = (value: T) => void

class Signal<T> {
  private listeners = new Set<Listener<T>>()
  on(listener: Listener<T>) { this.listeners.add(listener); return () => this.listeners.delete(listener) }
  emit(value: T) { this.listeners.forEach((listener) => listener(value)) }
}

export class ReplayTransport implements Transport {
  protected index = 0
  protected timer: number | null = null
  protected readonly sceneSignal = new Signal<ScenePayload>()
  protected readonly blockSignal = new Signal<BlockPayload>()
  protected readonly eventSignal = new Signal<EventPayload>()
  protected readonly statusSignal = new Signal<TransportStatus>()

  constructor(protected readonly run: DemoRun, private readonly cadenceMs = 1800) {}

  onScene(cb: Listener<ScenePayload>) { return this.sceneSignal.on(cb) }
  onBlock(cb: Listener<BlockPayload>) { return this.blockSignal.on(cb) }
  onEvent(cb: Listener<EventPayload>) { return this.eventSignal.on(cb) }
  onStatus(cb: Listener<TransportStatus>) { return this.statusSignal.on(cb) }
  command(_name: string, _args: Record<string, unknown>) {}

  start() {
    this.stop()
    queueMicrotask(() => {
      this.statusSignal.emit('replay')
      this.sceneSignal.emit(this.run.scene)
      this.emitCurrent()
    })
    this.timer = window.setInterval(() => {
      this.index = (this.index + 1) % this.run.blocks.length
      this.emitCurrent()
    }, this.cadenceMs)
  }

  stop() {
    if (this.timer !== null) window.clearInterval(this.timer)
    this.timer = null
  }

  seek(block: number) {
    const nextIndex = this.run.blocks.findIndex((item) => item.block === block)
    if (nextIndex < 0) throw new Error(`Block ${block} is not in this replay`)
    this.index = nextIndex
    this.emitCurrent()
  }

  protected emitCurrent() {
    const block = this.run.blocks[this.index]
    this.blockSignal.emit(structuredClone(block))
    this.run.events.filter((event) => event.block === block.block).forEach((event, eventIndex) => {
      window.setTimeout(() => this.eventSignal.emit({ ...event }), eventIndex * 80)
    })
  }
}

export class DemoTransport extends ReplayTransport {
  override start() {
    super.start()
    queueMicrotask(() => this.statusSignal.emit('live'))
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

  override seek(_block: number) {
    throw new Error('Seeking is only available in replay')
  }
}

export class LiveTransport implements Transport {
  private socket: WebSocket | null = null
  private failures = 0
  private stopped = false
  private reconnectTimer: number | null = null
  private staleTimer: number | null = null
  private readonly sceneSignal = new Signal<ScenePayload>()
  private readonly blockSignal = new Signal<BlockPayload>()
  private readonly eventSignal = new Signal<EventPayload>()
  private readonly statusSignal = new Signal<TransportStatus>()

  constructor(private readonly url: string) {}
  onScene(cb: Listener<ScenePayload>) { return this.sceneSignal.on(cb) }
  onBlock(cb: Listener<BlockPayload>) { return this.blockSignal.on(cb) }
  onEvent(cb: Listener<EventPayload>) { return this.eventSignal.on(cb) }
  onStatus(cb: Listener<TransportStatus>) { return this.statusSignal.on(cb) }

  start() { this.stopped = false; this.connect() }
  stop() {
    this.stopped = true
    this.socket?.close()
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    if (this.staleTimer !== null) window.clearTimeout(this.staleTimer)
  }

  command(name: string, args: Record<string, unknown>) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({ type: 'command', name, args }))
  }
  seek(_block: number) { throw new Error('Seeking is only available in replay') }

  private connect() {
    this.socket = new WebSocket(this.url)
    this.socket.onopen = () => { this.failures = 0; this.statusSignal.emit('live'); this.armStaleTimer() }
    this.socket.onmessage = (message) => {
      this.armStaleTimer()
      try {
        const payload = JSON.parse(String(message.data))
        if (payload.type === 'scene') this.sceneSignal.emit(payload.data)
        if (payload.type === 'block') this.blockSignal.emit(payload.data)
        if (payload.type === 'event') this.eventSignal.emit(payload.data)
      } catch {
        this.eventSignal.emit({ block: -1, agent: 'transport', kind: 'malformed_payload', text: 'Malformed live payload dropped' })
      }
    }
    this.socket.onclose = () => {
      if (this.stopped) return
      this.failures += 1
      this.statusSignal.emit(this.failures >= 3 ? 'disconnected' : 'stale')
      const delay = Math.min(5000, 500 * 2 ** (this.failures - 1))
      this.reconnectTimer = window.setTimeout(() => this.connect(), delay)
    }
  }

  private armStaleTimer() {
    if (this.staleTimer !== null) window.clearTimeout(this.staleTimer)
    this.staleTimer = window.setTimeout(() => this.statusSignal.emit('stale'), 5000)
  }
}
