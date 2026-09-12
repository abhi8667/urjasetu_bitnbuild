import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { City3D } from './City3D'
import { createDemoRun } from './demoFixture'
import { DemoTransport, ReplayTransport } from './transport'
import type { BlockPayload, EventPayload, RunSummary, ScenePayload, Transport, TransportStatus } from './types'

const screens = ['Grid overview', 'Energy impact', 'Agent theatre', 'DISCOM ledger', 'Household']
const agents = ['prosumer', 'consumer', 'market', 'sentinel', 'flow', 'market', 'settlement']
const phases = ['Gathering offers', 'Gathering bids', 'Clearing market', 'Constraint check', 'Reshaping flow', 'Re-clearing', 'Settling block']
const money = (value: number) => `₹${Math.round(value).toLocaleString('en-IN')}`
const title = (value: string) => value.charAt(0).toUpperCase() + value.slice(1)

type IconName = 'shield' | 'cloud' | 'pause' | 'play' | 'grid' | 'chart' | 'agents' | 'ledger' | 'home' | 'bolt' | 'arrow' | 'pin'
function Icon({ name }: { name: IconName }) {
  const paths = {
    shield: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />,
    cloud: <path d="M17.5 19H7a5 5 0 1 1 1.7-9.7A7 7 0 0 1 22 12.5 6.5 6.5 0 0 1 17.5 19Z" />,
    pause: <path d="M8 5v14M16 5v14" />,
    play: <path d="m8 5 11 7-11 7Z" />,
    grid: <><rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" /><rect x="3" y="14" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" /></>,
    chart: <><path d="M4 3v17h17M8 15l4-5 4 2 5-7" /></>,
    agents: <><circle cx="12" cy="5" r="3" /><circle cx="5" cy="18" r="3" /><circle cx="19" cy="18" r="3" /><path d="m10 8-4 7m8-7 4 7M8 18h8" /></>,
    ledger: <><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 8h6M9 12h6M9 16h3" /></>,
    home: <><path d="m3 10 9-7 9 7v10H3ZM9 20v-7h6v7" /></>,
    bolt: <path d="m13 2-9 12h7l-1 8 10-13h-7Z" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    pin: <><path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z" /><circle cx="12" cy="10" r="2" /></>,
  }
  return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>
}

function useGridTransport() {
  const liveRun = useMemo(createDemoRun, [])
  const transportRef = useRef<Transport | null>(null)
  const disconnectRef = useRef<(() => void) | null>(null)
  const [scene, setScene] = useState<ScenePayload | null>(null)
  const [block, setBlock] = useState<BlockPayload | null>(null)
  const [status, setStatus] = useState<TransportStatus>('stale')
  const [events, setEvents] = useState<EventPayload[]>([])
  const pendingCommands = useRef<Array<{ name: string; issuedAt: number; baselineExporters: number }>>([])

  const connect = useCallback((transport: Transport) => {
    disconnectRef.current?.()
    pendingCommands.current = []
    transportRef.current = transport
    const offs = [
      transport.onScene(setScene),
      transport.onBlock((next) => {
        if (!next || !['cleared', 'reshaped', 'fallback'].includes(next.status)) {
          setEvents((current) => [...current, { block: current.at(-1)?.block ?? -1, agent: 'transport', kind: 'malformed_payload', text: 'Malformed block dropped; holding last known state' }].slice(-40))
          return
        }
        pendingCommands.current = pendingCommands.current.filter((pending) => {
          const exporters = Object.values(next.houses).filter((house) => house.state === 'export').length
          const visible = pending.name === 'derate'
            ? next.status === 'reshaped' || Object.values(next.transformers).some((transformer) => transformer.stressed)
            : exporters < pending.baselineExporters
          if (visible) return false
          if (next.block >= pending.issuedAt + 2) {
            setEvents((current) => [...current, { block: next.block, agent: 'transport', kind: 'command_no_feedback', text: `${title(pending.name)} produced no visible change within two blocks` }].slice(-40))
            return false
          }
          return true
        })
        setBlock(next)
      }),
      transport.onEvent((event) => setEvents((current) => [...current, event].slice(-40))),
      transport.onStatus(setStatus),
    ]
    transport.start()
    disconnectRef.current = () => { offs.forEach((off) => off()); transport.stop() }
    return () => disconnectRef.current?.()
  }, [])

  useEffect(() => connect(new DemoTransport(liveRun)), [connect, liveRun])
  const command = useCallback((name: string) => {
    pendingCommands.current.push({ name, issuedAt: block?.block ?? 0, baselineExporters: Object.values(block?.houses ?? {}).filter((house) => house.state === 'export').length })
    transportRef.current?.command(name, name === 'derate' ? { transformer_id: 'DT-3', factor: 0.6 } : { cover: 0.8, blocks: 8 })
  }, [block])
  const replay = useCallback(() => { setEvents([]); connect(new ReplayTransport(createDemoRun())) }, [connect])
  return { scene, block, status, events, command, replay, summary: liveRun.summary }
}

function Shell({ active, onNavigate, status, replay, children }: { active: number; onNavigate: (value: number) => void; status: TransportStatus; replay: () => void; children: React.ReactNode }) {
  const navIcons: IconName[] = ['grid', 'chart', 'agents', 'ledger', 'home']
  return <div className={`app-shell ${status === 'stale' || status === 'disconnected' ? 'transport-alert' : ''}`}>
    <header className="topbar">
      <button className="brand" onClick={() => onNavigate(0)} aria-label="Open city screen"><span className="brand-mark"><Icon name="bolt" /></span><span><strong>UrjaSetu<span className="brand-dot">.</span></strong><small>Power belongs here.</small></span></button>
      <div className="workspace-label">Your workspace</div>
      <nav aria-label="Main screens">{screens.map((screen, index) => <button key={screen} className={`nav-item ${active === index ? 'active' : ''}`} onClick={() => onNavigate(index)} aria-label={screen} title={screen} aria-current={active === index ? 'page' : undefined}><Icon name={navIcons[index]} /><span>{screen}</span><kbd>{index + 1}</kbd></button>)}</nav>
      <div className="sidebar-story"><div className="solar-symbol"><Icon name="bolt" /></div><strong>A little more local.<br />A lot more resilient.</strong><p>Neighbors powering neighbors, one trade at a time.</p><span>Decentralized by design</span></div>
      <div className="workspace-location"><Icon name="pin" /><span><strong>Whitefield microgrid</strong><small>Bengaluru, India</small></span></div>
    </header>
    <div className="workspace-top"><div><span>Workspace</span><span className="breadcrumb-slash">/</span><strong>{screens[active]}</strong></div><div className="workspace-actions"><div className={`transport-status status-${status}`}><span />{status === 'live' ? 'Demo simulation' : status === 'replay' ? 'Replay run' : title(status)}</div><button className="replay-button" onClick={replay}><Icon name="play" />Replay</button><span className="operator-avatar" title="Grid operator">OP</span></div></div>
    {status === 'disconnected' && <div className="disconnect-note">Live link lost. Press <kbd>R</kbd> to switch to protected replay.</div>}
    <main>{children}</main>
    <footer className="key-rail"><span>Screen <kbd>1–5</kbd></span><span>Derate <kbd>D</kbd></span><span>Cloud bank <kbd>C</kbd></span><span>Replay <kbd>R</kbd></span><span className="key-rail-note">Demo data · 3D network model</span></footer>
  </div>
}

function CityScreen({ scene, block, status, events, command }: { scene: ScenePayload | null; block: BlockPayload | null; status: TransportStatus; events: EventPayload[]; command: (name: string) => void }) {
  const [selected, setSelected] = useState<string | null>(null)
  const traceRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (traceRef.current) traceRef.current.scrollTop = traceRef.current.scrollHeight }, [events])
  if (!scene) return <div className="waiting-state"><h1>Reading the network topology</h1><p>The city will remain available if the transport pauses.</p></div>
  const worst = block ? Object.entries(block.transformers).sort((a, b) => b[1].loading - a[1].loading)[0] : null
  const selectedHouse = selected ? scene.houses.find((house) => house.id === selected) : null
  const selectedReading = selected ? block?.houses[selected] : null
  const hidden = Math.max(0, (block?.trades.length ?? 0) - 12)
  const exporters = Object.values(block?.houses ?? {}).filter((house) => house.state === 'export').length
  const traded = block?.trades.reduce((total, trade) => total + trade.kwh, 0) ?? 0
  return <section className={`city-screen ${block?.status === 'reshaped' ? 'is-reshaped' : ''}`}>
    <header className="overview-heading"><div><h1>Your neighborhood. <span>Connected.</span></h1><p>Hyper-local energy. Autonomous agents. A more resilient grid.</p></div><div className="date-chip"><Icon name="pin" /><span>Whitefield, Bengaluru<small>{block ? `Day ${block.day} · ${block.clock} IST` : 'Connecting to simulation'}</small></span></div></header>
    <div className="metric-strip"><Metric label="Local energy traded" value={traded.toFixed(1)} note="kWh this block" /><Metric label="Clearing price" value={block?.clearing_price == null ? '—' : `₹${block.clearing_price.toFixed(2)}`} note="per kWh · peer-to-peer" /><Metric label="Solar exporters" value={String(exporters)} note={`of ${scene.houses.length} connected nodes`} /><Metric label="Highest grid loading" value={worst ? `${(worst[1].loading * 100).toFixed(0)}%` : '—'} note={`${worst?.[0] ?? 'Waiting'} · transformer capacity`} stress={Boolean(worst && worst[1].loading > 1)} /></div>
    <div className="city-stage">
      <div className="city-heading"><h2>Neighborhood network</h2><p>{scene.houses.length} nodes <span>·</span> {scene.transformers.length} transformers <span>·</span> Interactive 3D</p></div>
      <div className="legend"><span><i className="export-swatch" />Exporting</span><span><i className="import-swatch" />Importing</span><span><i className="stress-swatch" />Over limit</span></div>
      <select className="node-picker" aria-label="Inspect a network node" value={selected ?? ''} onChange={(event) => setSelected(event.target.value || null)}><option value="">Inspect a node</option>{scene.houses.map((house) => <option key={house.id} value={house.id}>{house.id} ? {house.transformer}</option>)}</select>
      <City3D scene={scene} block={block} selected={selected} onSelect={setSelected} />
      <p className="camera-hint">Drag to explore · Scroll to zoom · Select a home</p>
      {selectedHouse && <div className="asset-inspector"><button onClick={() => setSelected(null)} aria-label="Close inspection">×</button><span>{selectedHouse.transformer} · phase {selectedHouse.phase}</span><strong>{selectedHouse.id}</strong><p>{selectedReading ? `${Math.abs(selectedReading.net_kwh).toFixed(2)} kWh ${selectedReading.state === 'export' ? 'exported' : 'drawn'}` : 'Waiting for reading'}</p><small>{selectedHouse.has_pv ? 'Rooftop PV' : 'No PV'} · {selectedHouse.has_battery ? `${Math.round((selectedReading?.soc_frac ?? 0) * 100)}% battery` : 'No battery'}</small></div>}
      <div className="control-bar"><button onClick={() => command('derate')} disabled={status === 'replay'}><Icon name="shield" /><span>Derate DT-3<small>{status === 'replay' ? 'Replay' : 'D'}</small></span></button><button onClick={() => command('cloud')} disabled={status === 'replay'}><Icon name="cloud" /><span>Send cloud bank<small>{status === 'replay' ? 'Replay' : 'C'}</small></span></button></div>
    </div>
    <aside className="trace-panel"><div className="panel-heading"><h2>Agent activity</h2><span className="live-badge">{block ? `Block ${block.block}` : 'Standby'}</span></div><div className={`agent-status ${worst && worst[1].loading > 1 ? 'attention' : ''}`}><Icon name="shield" /><div><strong>{worst && worst[1].loading > 1 ? 'Sentinel is watching' : 'Your grid is in good hands'}</strong><small>{block?.status === 'reshaped' ? 'Power flow reshaped by agents' : block?.status === 'fallback' ? 'Safe curtailment applied' : 'Monitoring every local connection'}</small></div></div><div className="trace-lines" ref={traceRef}>{events.length ? events.map((event, index) => <p key={`${event.block}-${event.kind}-${index}`}><span><i />{title(event.agent)}<small>#{event.block}</small></span>{event.text}</p>) : <p className="quiet-line">Waiting for the first agent event.</p>}</div><div className="trace-footer"><span className="status-dot" />{block ? title(block.status) : 'Waiting'}<small>{hidden ? `12 paths · ${hidden} grouped` : `${block?.trades.length ?? 0} trade paths`}</small></div></aside>
    <section className="transformer-panel"><div className="section-title"><h2>Transformer health</h2><span>Capacity utilization this block</span></div><div className="transformer-cards">{scene.transformers.map((transformer) => { const reading = block?.transformers[transformer.id]; return <div className={`transformer-card ${reading?.stressed ? 'stressed' : ''}`} key={transformer.id}><div className="transformer-card-top"><span className="transformer-icon"><Icon name="bolt" /></span><strong>{transformer.id}<small>{transformer.rating_kva} kVA capacity</small></strong><span className="health-pill">{reading ? reading.stressed ? 'Over limit' : 'Healthy' : 'Waiting'}</span></div><div className="transformer-reading"><strong>{reading ? Math.round(reading.loading * 100) : '—'}<small>%</small></strong><span>{reading ? `${reading.hotspot_c.toFixed(1)} °C` : '—'} hot-spot</span></div><div className="bar"><i style={{ width: `${Math.min(100, (reading?.loading ?? 0) * 100)}%` }} /></div></div> })}</div></section>
  </section>
}

function Metric({ label, value, note, clock, stress, state }: { label: string; value: string; note: string; clock?: boolean; stress?: boolean; state?: string }) {
  return <div className={`${clock ? 'clock-metric' : ''} ${stress ? 'metric-stress' : ''} ${state ? `state-${state}` : ''}`}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
}

function Page({ number, titleText, subtitle, children }: { number: number; titleText: string; subtitle: string; children: React.ReactNode }) {
  return <section className="secondary-screen"><header className="screen-heading"><span>Screen {number} of 5</span><h1>{titleText}</h1><p>{subtitle}</p></header>{children}</section>
}

function Compare({ summary }: { summary: RunSummary }) {
  const rows = [['Household bill', money(summary.householdBillBaseline), money(summary.householdBillUrjasetu), `−${money(summary.householdBillBaseline - summary.householdBillUrjasetu)}`], ['DISCOM revenue', money(summary.discomRevenueBaseline), money(summary.discomRevenueUrjasetu), `+${money(summary.discomRevenueUrjasetu - summary.discomRevenueBaseline)}`], ['Transformer life used', `${summary.transformerLifeBaseline.toFixed(3)}%`, `${summary.transformerLifeUrjasetu.toFixed(3)}%`, `−${Math.round((1 - summary.transformerLifeUrjasetu / summary.transformerLifeBaseline) * 100)}%`]]
  return <Page number={2} titleText="Energy impact" subtitle="The same street, settled two ways."><div className="compare-table"><div className="compare-row compare-head"><span /><span>Net metering</span><span>UrjaSetu</span><span>Delta</span></div>{rows.map((row) => <div className="compare-row" key={row[0]}><strong>{row[0]}</strong><span>{row[1]}</span><span>{row[2]}</span><span className="favourable">{row[3]}</span></div>)}</div><p className="run-note">Thirty simulated days across {summary.houses} network nodes and {summary.transformers} distribution transformers.</p></Page>
}

function Theatre({ events, block }: { events: EventPayload[]; block: BlockPayload | null }) {
  const [step, setStep] = useState(0), [paused, setPaused] = useState(false)
  useEffect(() => { if (paused) return; const timer = window.setInterval(() => setStep((value) => (value + 1) % agents.length), 1600); return () => window.clearInterval(timer) }, [paused])
  useEffect(() => {
    const onSpace = (event: KeyboardEvent) => { if (event.code === 'Space' && !(event.target instanceof HTMLElement && event.target.closest('button, input, select, textarea'))) { event.preventDefault(); setPaused((value) => !value) } }
    window.addEventListener('keydown', onSpace); return () => window.removeEventListener('keydown', onSpace)
  }, [])
  const active = agents[step], message = [...events].reverse().find((event) => event.agent === active)?.text ?? `${title(active)} is ready for the next event.`
  return <Page number={3} titleText="Agent theatre" subtitle="One block, seven deliberate hand-offs."><div className="theatre-layout"><div className="phase-rail"><span>Current phase</span><strong>{phases[step]}</strong><div className="progress-track"><i style={{ width: `${((step + 1) / agents.length) * 100}%` }} /></div><small>Step {step + 1} of {agents.length}</small></div><div className="agent-grid">{agents.map((agent, index) => <div key={`${agent}-${index}`} className={`agent-chip ${index === step ? 'active' : ''} ${index < step ? 'complete' : ''}`}><span>{index + 1}</span><strong>{agent === 'market' && index === 5 ? 'market re-clear' : agent}</strong></div>)}</div><div className="message-panel"><span>{active}</span><p>{message}</p><small>block {block?.block ?? '—'} / ordered event stream</small></div><button className="pause-button" onClick={() => setPaused(!paused)}><Icon name={paused ? 'play' : 'pause'} />{paused ? 'Continue sequence' : 'Pause on this step'}</button></div></Page>
}

function Ledger({ block, summary }: { block: BlockPayload | null; summary: RunSummary }) {
  const [subsidy, setSubsidy] = useState(false), entries = Object.entries(block?.transformers ?? {}).sort((a, b) => b[1].loading - a[1].loading)
  return <Page number={4} titleText="DISCOM ledger" subtitle="Revenue and asset health in the same instrument."><div className="ledger-topline"><div><span>Charges collected</span><strong>{money(summary.discomRevenueUrjasetu + (subsidy ? 684 : 0))}</strong><small>Wheeling + transactions{subsidy ? ' + cross-subsidy' : ''}</small></div><div><span>Replacement deferred</span><strong>{money(summary.deferredCapex)}</strong><small>Modelled asset-life value</small></div><label className="switch-row"><input type="checkbox" checked={subsidy} onChange={(event) => setSubsidy(event.target.checked)} /><span><strong>Cross-subsidy surcharge</strong><small>Apply configured DISCOM charge</small></span></label></div><div className="fleet-table"><div className="fleet-row fleet-head"><span>Transformer</span><span>Loading</span><span>Hot-spot</span><span>Life used</span><span>Risk</span></div>{entries.map(([id, item], index) => <div className={`fleet-row ${item.stressed ? 'row-stressed' : ''}`} key={id}><strong>{id}</strong><span>{(item.loading * 100).toFixed(0)}%</span><span>{item.hotspot_c.toFixed(1)} °C</span><span>{(item.life_used_frac * 100).toFixed(4)}%</span><span>{index + 1} / {entries.length}</span></div>)}</div></Page>
}

function Household({ scene, block }: { scene: ScenePayload | null; block: BlockPayload | null }) {
  const houses = useMemo(() => scene?.houses.filter((house) => house.kind !== 'evhub') ?? [], [scene])
  const [selected, setSelected] = useState('H-08'), [aggression, setAggression] = useState(1)
  const house = houses.find((item) => item.id === selected) ?? houses[0], reading = house ? block?.houses[house.id] : null
  const savings = house ? 228 + Number(house.id.replace('H-', '')) * 3 : 0, spend = Math.min(100, 42 + aggression * 17)
  return <Page number={5} titleText="Household" subtitle="One clear view of a home’s energy position."><div className="household-selector"><label htmlFor="house-select">Active premises</label><select id="house-select" value={house?.id} onChange={(event) => setSelected(event.target.value)}>{houses.map((item) => <option value={item.id} key={item.id}>{item.id} · {item.transformer}{item.has_pv ? ' · rooftop solar' : ''}</option>)}</select></div><div className="household-layout"><section className="earnings-figure"><span>Saved this month</span><strong>{money(savings)}</strong><p>{reading ? `${Math.abs(reading.net_kwh).toFixed(2)} kWh ${reading.state === 'export' ? 'available to neighbours' : 'local demand'} now` : 'Waiting for reading'}</p></section><section className="household-controls"><Bar label="Battery state" value={reading?.soc_frac == null ? 0 : reading.soc_frac * 100} copy={reading?.soc_frac == null ? 'Not installed' : `${Math.round(reading.soc_frac * 100)}%`} /><div><div className="control-heading"><span>Buying approach</span><strong>{['Careful', 'Balanced', 'Active'][aggression]}</strong></div><div className="dial-options">{['Careful', 'Balanced', 'Active'].map((label, index) => <button key={label} className={aggression === index ? 'active' : ''} onClick={() => setAggression(index)}>{label}</button>)}</div></div><Bar label="Monthly spend cap" value={spend} copy={`₹${Math.round(spend * 24)} / ₹2,400`} /></section><p className="savings-note">This home is {money(savings)} ahead of DISCOM-only supply this month.</p></div></Page>
}

function Bar({ label, value, copy }: { label: string; value: number; copy: string }) { return <div><div className="control-heading"><span>{label}</span><strong>{copy}</strong></div><div className="bar"><i style={{ width: `${value}%` }} /></div></div> }

export default function App() {
  const [screen, setScreen] = useState(0)
  const { scene, block, status, events, command, replay, summary } = useGridTransport()
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return
      if (/^[1-5]$/.test(event.key)) setScreen(Number(event.key) - 1)
      if (event.key.toLowerCase() === 'd' && status !== 'replay') command('derate')
      if (event.key.toLowerCase() === 'c' && status !== 'replay') command('cloud')
      if (event.key.toLowerCase() === 'r') replay()
    }
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey)
  }, [command, replay, status])
  const content = [<CityScreen scene={scene} block={block} status={status} events={events} command={command} />, <Compare summary={summary} />, <Theatre events={events} block={block} />, <Ledger block={block} summary={summary} />, <Household scene={scene} block={block} />][screen]
  return <Shell active={screen} onNavigate={setScreen} status={status} replay={replay}>{content}</Shell>
}
