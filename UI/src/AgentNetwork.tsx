import { Component, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, Line, OrbitControls, Stars } from '@react-three/drei'
import * as THREE from 'three'
import type { BlockPayload, EventPayload, TransportStatus } from './types'
import { activityEventKey, useActivityScroll } from './activityScroll'
import './agent-network.css'

type Family = 'ML' | 'LLM' | 'Logic' | 'System' | 'Governance'
type Agent = { id: string; name: string; family: Family; position: [number, number, number]; detail: string }
const colors: Record<Family, string> = { ML: '#50e4ed', LLM: '#bc8aff', Logic: '#ffbd69', System: '#78e3b0', Governance: '#a78bfa' }
const agents: Agent[] = [
  { id: 'market', name: 'Market', family: 'Logic', position: [0, 0, 0], detail: 'Matches local bids and offers, then clears the energy market.' },
  { id: 'grid_risk', name: 'Grid risk', family: 'ML', position: [-7.5, 4.3, -1], detail: 'Logistic regression trained on the simulated meter feed predicts transformer overload probability every block and supplies risk scores to the trading AI. These are simulation predictions, not validated field forecasts.' },
  { id: 'ai_trading', name: 'Trading strategy', family: 'LLM', position: [-2.7, 5.6, 0], detail: 'Groq strategy advisor receives ML risk scores and market conditions once per simulated day. Its bounded decision reaches every trader. The stream reports new decisions, reuse, disabled configuration, and fallback.' },
  { id: 'prosumer', name: 'Prosumers', family: 'Logic', position: [-8.8, .2, 2], detail: 'Agent pool offering surplus rooftop solar and battery energy.' },
  { id: 'consumer', name: 'Consumers', family: 'Logic', position: [-5.2, -4.5, 1], detail: 'Agent pool submitting energy bids within household spend caps.' },
  { id: 'sentinel', name: 'Grid sentinel', family: 'Logic', position: [6.8, 4.1, -1], detail: 'Checks transformer constraints and detects predicted or actual breaches.' },
  { id: 'flow', name: 'Power flow', family: 'Logic', position: [8.3, -.4, 1], detail: 'Reshapes trades and coordinates batteries to keep the grid within limits.' },
  { id: 'health', name: 'Asset health', family: 'Logic', position: [4.2, -5.4, -1], detail: 'Tracks transformer thermal ageing and insulation life.' },
  { id: 'settlement', name: 'Settlement', family: 'Logic', position: [-.2, -6.4, 2], detail: 'Posts itemised bills and reconciles the cleared energy trades.' },
  { id: 'runner', name: 'Orchestrator', family: 'System', position: [.4, 1.8, -6.5], detail: 'Opens simulation blocks and coordinates the agent pipeline.' },
  { id: 'battery', name: 'Battery dispatch', family: 'System', position: [9.2, -4.4, -3], detail: 'Reports battery movement in response to grid decisions.' },
  { id: 'governance', name: 'Governance', family: 'Governance', position: [-6.7, -6.5, -2], detail: 'Rule-based compliance agent (GC-01–GC-12). Audits every block for transformer breaches, settlement reconciliation, curtailment concentration, hot-spot violations, and per-household fairness. Fully deterministic — identical input produces identical findings.' },
  { id: 'ops_briefing', name: 'Ops briefing', family: 'Governance', position: [-10.2, -3.5, -3], detail: 'Reads the governance audit and turns it into a plain-language daily briefing for non-technical administrators: what changed, what needs attention, what action is recommended. Template-based by default; LLM-enriched via Groq when configured.' },
]
const routes = [
  ['runner', 'grid_risk'], ['grid_risk', 'ai_trading'], ['ai_trading', 'prosumer'], ['ai_trading', 'consumer'],
  ['prosumer', 'market'], ['consumer', 'market'], ['runner', 'market'], ['market', 'sentinel'],
  ['sentinel', 'flow'], ['flow', 'market'], ['flow', 'battery'], ['flow', 'health'], ['health', 'settlement'], ['market', 'settlement'],
  ['settlement', 'governance'], ['health', 'governance'], ['sentinel', 'governance'], ['governance', 'ops_briefing'],
]
const agentId = (event: EventPayload) => ({ risk: 'grid_risk', strategy: 'ai_trading', llm: 'ai_trading' }[event.agent] ?? event.agent)

function EventText({ event, onExpand }: { event: EventPayload; onExpand?: () => void }) {
  if (event.kind !== 'ai_strategy_thinking') return <p>{event.text}</p>
  const preview = event.text.length > 240 ? `${event.text.slice(0, 240).trimEnd()}...` : event.text
  return <details className="reasoning-details" onToggle={(toggleEvent) => {
    if (toggleEvent.currentTarget.open) onExpand?.()
  }}>
    <summary><span>{preview}</span><em>Show full reasoning</em></summary>
    <p>{event.text}</p>
  </details>
}

function newestBlocksFirst(events: EventPayload[]) {
  const groups: EventPayload[][] = []
  for (const event of events.slice(-20)) {
    const group = groups.at(-1)
    if (!group || group[0].block !== event.block) groups.push([event])
    else group.push(event)
  }
  // Newest block first, chronological order inside a block, so an LLM's
  // thinking remains visibly above the final strategy that it produced.
  return groups.reverse().flat()
}

class SceneBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? <div className="network-fallback">3D rendering is unavailable. Explore agents and their events using the panel.</div> : this.props.children }
}

function Bubble({ agent, selected, active, onSelect, paused }: { agent: Agent; selected: boolean; active: boolean; onSelect: () => void; paused: boolean }) {
  const halo = useRef<THREE.Mesh>(null)
  const time = useRef(0)
  const color = colors[agent.family]
  const radius = agent.id === 'market' ? .85 : .55
  useFrame((_, delta) => {
    if (!paused) time.current += delta
    if (halo.current) halo.current.scale.setScalar(1.12 + (active ? Math.sin(time.current * 4) * .09 : 0))
  })
  return <group position={agent.position}>
    <mesh onClick={(e) => { e.stopPropagation(); onSelect() }}>
      <sphereGeometry args={[radius, 40, 40]} />
      <meshStandardMaterial color={color} emissive={color} emissiveIntensity={selected ? 1.1 : .45} metalness={.6} roughness={.2} />
    </mesh>
    <mesh ref={halo}>
      <sphereGeometry args={[radius, 24, 24]} />
      <meshBasicMaterial color={color} wireframe transparent opacity={selected ? .25 : .09} />
    </mesh>
    <mesh rotation={[Math.PI / 2.7, .35, 0]}>
      <torusGeometry args={[radius * 1.5, .014, 8, 70]} />
      <meshBasicMaterial color={color} transparent opacity={selected || active ? .9 : .3} />
    </mesh>
    <Html position={[0, -radius - .45, 0]} center distanceFactor={19} zIndexRange={[30, 0]}>
      <button className={`bubble-label ${selected ? 'selected' : ''}`} style={{ '--agent-color': color } as CSSProperties} onClick={onSelect} aria-pressed={selected}>
        <span>{agent.name}</span><small>{agent.family}{active ? ' / transmitting' : ''}</small>
      </button>
    </Html>
  </group>
}

function Connection({ from, to, active, highlighted, paused }: { from: Agent; to: Agent; active: boolean; highlighted: boolean; paused: boolean }) {
  const packet = useRef<THREE.Mesh>(null)
  const progress = useRef(0)
  const curve = useMemo(() => {
    const start = new THREE.Vector3(...from.position), end = new THREE.Vector3(...to.position)
    const mid = start.clone().lerp(end, .5)
    mid.z += 1.4
    return new THREE.QuadraticBezierCurve3(start, mid, end)
  }, [from, to])
  const points = useMemo(() => curve.getPoints(45), [curve])
  useFrame((_, delta) => {
    if (!paused) progress.current = (progress.current + delta * .42) % 1
    if (packet.current) packet.current.position.copy(curve.getPoint(progress.current))
  })
  return <group>
    <Line points={points} color={colors[from.family]} transparent opacity={active ? .65 : highlighted ? .38 : .1} lineWidth={active ? 1.8 : 1} />
    {active && <mesh ref={packet}><sphereGeometry args={[.085, 12, 12]} /><meshBasicMaterial color={colors[from.family]} /></mesh>}
  </group>
}

export function AgentNetwork({ events, status, offline, block, synchronized, onClose }: { events: EventPayload[]; status: TransportStatus; offline: boolean; block: BlockPayload | null; synchronized: boolean; onClose?: () => void }) {
  const [selected, setSelected] = useState<string | null>(null)
  const [filter, setFilter] = useState<Family | 'All'>('All')
  const [paused, setPaused] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const [rotate, setRotate] = useState(false)
  const [reset, setReset] = useState(0)
  const [active, setActive] = useState<Set<string>>(new Set())
  const lastSeen = useRef<EventPayload | undefined>(undefined)
  useEffect(() => {
    const last = events.at(-1)
    if (last?.block !== block?.block) { setActive(new Set()); return }
    if (!last || last === lastSeen.current) return
    const previous = lastSeen.current ? events.indexOf(lastSeen.current) : -1
    lastSeen.current = last
    setActive(new Set(events.slice(previous + 1).filter(event => event.block === block?.block).map(agentId)))
    const timeout = window.setTimeout(() => setActive(new Set()), 4500)
    return () => window.clearTimeout(timeout)
  }, [events, block?.block])
  const visible = agents.filter((agent) => filter === 'All' || agent.family === filter)
  const current = agents.find((agent) => agent.id === selected)
  const filteredEvents = newestBlocksFirst(events.filter((event) => (!selected || agentId(event) === selected) && (filter === 'All' || agents.some((a) => a.id === agentId(event) && a.family === filter))))
  const feedScroll = useActivityScroll(`${selected ?? ''}:${filter}:${activityEventKey(events.at(-1) ?? events)}`, 'top')
  return <main className="agent-network">
    <header className="network-header">
      <a
        href="#/"
        className="network-back"
        onClick={(e) => {
          if (onClose) {
            e.preventDefault()
            onClose()
          }
        }}
        title={onClose ? "Close to 3D City" : "Back to City"}
      >
        ← <span>UrjaSetu</span>
      </a>
      <span className="network-breadcrumb">INTELLIGENCE / AGENT NETWORK</span>
      <span className="network-source"><i />{!synchronized ? 'CITY DISCONNECTED' : offline ? 'SYNCED DEMO' : `CITY SYNCED / ${status.toUpperCase()}`} </span>
    </header>
    <div className="network-layout">
      <section className="network-stage" aria-label="Interactive 3D agent communication network">
        <div className="network-heading"><span className="network-eyebrow">A COLLECTIVE INTELLIGENCE</span><h1>Every agent.<br /><em>One connected grid.</em></h1><p>{synchronized && block ? `Day ${block.day} | ${block.clock} | Block ${block.block} | ${block.trades.length} trades` : 'Open Agent Stream from the city tab to connect. Keep that tab open.'}</p></div>
        <div className="network-stage-summary"><span><b>{agents.length}</b> agent roles</span><span><b>{routes.length}</b> workflow paths</span></div>
        <SceneBoundary><Canvas key={reset} gl={{ alpha: true }} camera={{ position: [0, 0, 26], fov: 44 }} dpr={[1, 1.5]} onPointerMissed={() => setSelected(null)} fallback={<div className="network-fallback">WebGL unavailable. Use the agent panel to explore activity.</div>}>
          <ambientLight intensity={1.2} /><pointLight position={[0, 6, 8]} intensity={65} color="#b9d9ff" />
          <Stars radius={65} depth={35} count={1100} factor={2} saturation={0} fade speed={paused ? 0 : .3} />
          <group position={[0, -.4, 0]}>
            {routes.map(([source, target]) => {
              const from = agents.find(a => a.id === source)!, to = agents.find(a => a.id === target)!
              if (filter !== 'All' && from.family !== filter && to.family !== filter) return null
              return <Connection key={`${source}-${target}`} from={from} to={to} active={synchronized && active.has(source)} highlighted={selected === source || selected === target} paused={paused} />
            })}
            {agents.map(agent => <Bubble key={agent.id} agent={agent} selected={selected === agent.id} active={synchronized && active.has(agent.id)} onSelect={() => { setSelected(agent.id); setFilter('All') }} paused={paused} />)}
          </group>
          <OrbitControls makeDefault enablePan={false} minDistance={16} maxDistance={38} autoRotate={rotate && !paused} autoRotateSpeed={.35} />
        </Canvas></SceneBoundary>
        <div className="network-tools"><button onClick={() => setPaused(!paused)} aria-pressed={paused}>{paused ? '▶ Resume motion' : 'Ⅱ Pause motion'}</button><button onClick={() => setRotate(!rotate)} aria-pressed={rotate}>Orbit {rotate ? 'on' : 'off'}</button><button onClick={() => setReset(reset + 1)}>Reset view</button></div>
        <div className="network-stage-footer"><span>DRAG TO ORBIT · SCROLL TO ZOOM · SELECT A BUBBLE</span><span>Particles follow outgoing workflow paths →</span></div>
      </section>
      <aside className="network-panel">
        <div className="network-panel-title"><span>NETWORK EXPLORER</span><span className="network-count">{agents.length} roles</span></div>
        <div className="network-filters">{(['All', 'ML', 'LLM', 'Logic', 'System', 'Governance'] as const).map(family => <button key={family} aria-pressed={filter === family} onClick={() => { setFilter(family); setSelected(null) }} style={{ '--agent-color': family === 'All' ? '#fff' : colors[family] } as CSSProperties}>{family !== 'All' && <i />}{family}</button>)}</div>
        <div className="network-agent-list">{visible.map(agent => <button key={agent.id} onClick={() => setSelected(selected === agent.id ? null : agent.id)} aria-pressed={selected === agent.id}><i style={{ background: colors[agent.family] }} /><span>{agent.name}</span><small>{agent.family}</small></button>)}</div>
        {current && <div className="network-detail"><div><strong style={{ color: colors[current.family] }}>{current.name}</strong><button onClick={() => setSelected(null)} aria-label="Clear agent selection">×</button></div><p>{current.detail}</p><small>{events.filter(e => agentId(e) === current.id).length} events in current window</small><p className="network-paths">Sends to: {routes.filter(([from]) => from === current.id).map(([, to]) => agents.find(a => a.id === to)!.name).join(', ') || 'End of workflow'}</p></div>}
        <div className="network-feed-title"><h2>{current ? 'Agent activity' : 'Activity stream'}</h2><span>{filteredEvents.length} recent</span></div>
        <div className="network-feed" ref={feedScroll.ref} onScroll={feedScroll.onScroll} onWheel={feedScroll.onUserScroll} onPointerDown={feedScroll.onUserScroll} role="log" aria-label="Recent agent events">{filteredEvents.length ? filteredEvents.map((event) => {
          const agent = agents.find(a => a.id === agentId(event))
          return <article className={event.kind === 'ai_strategy_thinking' ? 'is-thinking' : ''} key={activityEventKey(event)} style={{ '--agent-color': agent ? colors[agent.family] : '#78e3b0' } as CSSProperties}><div><strong>{agent?.name ?? event.agent}{event.kind === 'ai_strategy_thinking' && <em className="thinking-badge">THINKING</em>}</strong><span>BLOCK {event.block}</span></div><EventText event={event} onExpand={feedScroll.syncAfterLayout} /><small>{event.kind.replaceAll('_', ' ')}</small></article>
        }) : <p className="network-empty">No events received{current ? ` from ${current.name}` : ''}. Waiting for activity in this stream.</p>}</div>
        <p className="network-note">{offline ? 'Demo events. ' : ''}Links show the documented workflow; recipients are inferred. Bubbles represent agent roles; consumer and prosumer pools are grouped.</p>
      </aside>
    </div>
  </main>
}
