import { useEffect, useRef, useState } from 'react'
import { AgentNetwork } from './AgentNetwork'
import type { BlockPayload, EventPayload, TransportStatus } from './types'

export type CitySnapshot = { block: BlockPayload | null; events: EventPayload[]; status: TransportStatus; offline: boolean }
const channelName = (source: string) => `urjasetu-city-${source}`

// Each city owns a channel. Network tabs never create a transport or simulation.
export function useCityBroadcast(snapshot: CitySnapshot) {
  const [source] = useState(() => crypto.randomUUID())
  const latest = useRef(snapshot)
  const channel = useRef<BroadcastChannel | null>(null)
  useEffect(() => {
    const bus = new BroadcastChannel(channelName(source))
    channel.current = bus
    const publish = () => bus.postMessage({ type: 'snapshot', snapshot: latest.current })
    bus.onmessage = (event) => { if (event.data?.type === 'subscribe') publish() }
    const heartbeat = window.setInterval(publish, 1000)
    return () => { window.clearInterval(heartbeat); bus.close(); channel.current = null }
  }, [source])
  useEffect(() => {
    latest.current = snapshot
    channel.current?.postMessage({ type: 'snapshot', snapshot })
  }, [snapshot.block, snapshot.events, snapshot.status, snapshot.offline])
  return `#/agents?source=${source}`
}

export function SyncedAgentNetwork({ source }: { source: string | null }) {
  const [snapshot, setSnapshot] = useState<CitySnapshot | null>(null)
  const [connected, setConnected] = useState(false)
  useEffect(() => {
    if (!source) return
    const bus = new BroadcastChannel(channelName(source))
    let lastReceived = 0
    bus.onmessage = (event) => {
      if (event.data?.type !== 'snapshot') return
      const next = event.data.snapshot as CitySnapshot
      lastReceived = Date.now()
      setConnected(true)
      // Heartbeats should not retrigger packet animation.
      setSnapshot(previous => previous && JSON.stringify(previous) === JSON.stringify(next) ? previous : next)
    }
    bus.postMessage({ type: 'subscribe' })
    const watchdog = window.setInterval(() => {
      if (Date.now() - lastReceived > 5000) setConnected(false)
      bus.postMessage({ type: 'subscribe' })
    }, 2000)
    return () => { window.clearInterval(watchdog); bus.close() }
  }, [source])
  return <AgentNetwork events={snapshot?.events ?? []} status={snapshot?.status ?? 'connecting'} offline={snapshot?.offline ?? false} block={snapshot?.block ?? null} synchronized={connected} />
}
