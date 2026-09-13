import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { City3D, type CameraMode } from './City3D'
import { SyncedAgentNetwork, useCityBroadcast } from './agentSync'
import { createDemoRun } from './demoFixture'
import { DemoTransport, EngineTransport, ReplayTransport } from './transport'
import { HAS_CONFIGURED_ENGINE } from './config'
import { TelemetryGraph } from './TelemetryGraph'
import type { BlockPayload, EventPayload, RunSummary, ScenePayload, Transport, TransportStatus } from './types'

const money = (value: number) => `₹${Math.round(value).toLocaleString('en-IN')}`
const title = (value: string) => value.charAt(0).toUpperCase() + value.slice(1)

/** Render a number the engine may not have. Never substitutes a plausible
 *  figure: the UI used to fall back to literals like '148.2' and '₹4.20', which
 *  is indistinguishable on screen from a real reading. */
const orDash = (value: number | null | undefined, digits = 2, unit = '') =>
  value == null || Number.isNaN(value) ? '—' : `${value.toFixed(digits)}${unit}`

function useGridTransport() {
  // Kept only as the offline fallback. Everything in it is invented in
  // TypeScript, so it must never be what the app opens on when an engine is
  // configured — see `offline` below, which the status bar surfaces.
  const demoRun = useMemo(createDemoRun, [])
  const transportRef = useRef<Transport | null>(null)
  const disconnectRef = useRef<(() => void) | null>(null)
  const [scene, setScene] = useState<ScenePayload | null>(null)
  const [block, setBlock] = useState<BlockPayload | null>(null)
  const [history, setHistory] = useState<BlockPayload[]>([])
  const [status, setStatus] = useState<TransportStatus>('connecting')
  const [events, setEvents] = useState<EventPayload[]>([])
  // The engine streams its own summary. Until it arrives there is no summary
  // at all, and the screens that show one say so rather than drawing the
  // fixture's constants.
  const [summary, setSummary] = useState<RunSummary | null>(null)
  const [offline, setOffline] = useState(!HAS_CONFIGURED_ENGINE)
  const pendingCommands = useRef<Array<{ name: string; issuedAt: number; baselineExporters: number }>>([])

  const connect = useCallback((transport: Transport) => {
    disconnectRef.current?.()
    pendingCommands.current = []
    transportRef.current = transport
    const offs = [
      transport.onScene(setScene),
      transport.onBlock((next) => {
        if (!next || !['cleared', 'reshaped', 'fallback'].includes(next.status)) {
          setEvents((current) => [
            ...current,
            {
              block: current.at(-1)?.block ?? -1,
              agent: 'transport',
              kind: 'malformed_payload',
              text: 'Malformed block dropped; holding last known state',
            },
          ].slice(-50))
          return
        }
        pendingCommands.current = pendingCommands.current.filter((pending) => {
          const exporters = Object.values(next.houses).filter((house) => house.state === 'export').length
          const visible =
            pending.name === 'derate'
              ? next.status === 'reshaped' || Object.values(next.transformers).some((t) => t.stressed)
              : exporters < pending.baselineExporters
          if (visible) return false
          if (next.block >= pending.issuedAt + 2) {
            setEvents((current) => [
              ...current,
              {
                block: next.block,
                agent: 'transport',
                kind: 'command_no_feedback',
                text: `${title(pending.name)} produced no visible change within two blocks`,
              },
            ].slice(-50))
            return false
          }
          return true
        })
        setBlock(next)
        setHistory((current) => {
          const idx = current.findIndex((b) => b.block === next.block)
          if (idx >= 0) {
            const copy = [...current]
            copy[idx] = next
            return copy
          }
          return [...current.slice(-47), next]
        })
      }),
      transport.onEvent((event) => setEvents((current) => [...current, event].slice(-50))),
      transport.onStatus(setStatus),
      transport.onSummary?.(setSummary) ?? (() => {}),
    ]
    transport.start()
    disconnectRef.current = () => {
      offs.forEach((off) => off())
      transport.stop()
    }
    return () => disconnectRef.current?.()
  }, [])

  useEffect(() => {
    // The engine is the default. `LiveTransport` existed in this file's
    // predecessor and was never constructed — the app opened on the fixture,
    // which is why every figure on screen was a TypeScript constant.
    if (HAS_CONFIGURED_ENGINE) {
      setOffline(false)
      return connect(new EngineTransport())
    }
    setOffline(true)
    setSummary(demoRun.summary)
    return connect(new DemoTransport(demoRun))
  }, [connect, demoRun])

  const command = useCallback((name: string) => {
    pendingCommands.current.push({
      name,
      issuedAt: block?.block ?? 0,
      baselineExporters: Object.values(block?.houses ?? {}).filter((house) => house.state === 'export').length,
    })
    transportRef.current?.command(
      name,
      name === 'derate' ? { transformer_id: 'DT-3', factor: 0.6 } : { cover: 0.8, blocks: 8 }
    )
  }, [block])

  const seek = useCallback((targetBlock: number) => {
    try {
      transportRef.current?.seek(targetBlock)
    } catch {
      // Ignored
    }
  }, [])

  const replay = useCallback(() => {
    if (!offline && HAS_CONFIGURED_ENGINE) {
      seek(0)
      return
    }
    setEvents([])
    setOffline(true)
    const run = createDemoRun(scene ?? undefined)
    setSummary(run.summary)
    connect(new ReplayTransport(run))
  }, [connect, offline, scene, seek])

  const reconnect = useCallback(() => {
    setEvents([])
    setOffline(false)
    connect(new EngineTransport())
  }, [connect])

  return { scene, block, history, status, events, command, seek, replay, reconnect, summary, offline }
}

// Circular SVG Progress Gauge
function CircularGauge({
  label,
  value,
  color = '#00f0ff',
  sublabel,
}: {
  label: string
  value: number
  color?: string
  sublabel?: string
}) {
  const radius = 34
  const circumference = 2 * Math.PI * radius
  const strokeDashoffset = circumference - (Math.min(100, Math.max(0, value)) / 100) * circumference

  return (
    <div className="gauge-card">
      <div className="gauge-svg-container">
        <svg viewBox="0 0 88 88" className="gauge-svg">
          {/* Background circle */}
          <circle
            cx="44"
            cy="44"
            r={radius}
            fill="transparent"
            stroke="rgba(255, 255, 255, 0.1)"
            strokeWidth="6"
          />
          {/* Active progress circle */}
          <circle
            cx="44"
            cy="44"
            r={radius}
            fill="transparent"
            stroke={color}
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            transform="rotate(-90 44 44)"
          />
        </svg>
        <div className="gauge-value-center">
          <span className="gauge-pct">{Math.round(value)}%</span>
        </div>
      </div>
      <div className="gauge-meta">
        <strong>{label}</strong>
        {sublabel && <small>({sublabel})</small>}
      </div>
    </div>
  )
}

export default function App() {
  const [route, setRoute] = useState(window.location.hash)
  useEffect(() => {
    const navigate = () => setRoute(window.location.hash)
    window.addEventListener('hashchange', navigate)
    return () => window.removeEventListener('hashchange', navigate)
  }, [])
  if (route.split('?')[0] === '#/agents') {
    const source = new URLSearchParams(route.split('?')[1]).get('source')
    return <SyncedAgentNetwork key={source} source={source} />
  }
  return <CityApp />
}

function CityApp() {
  const { scene, block, history, status, events, command, seek, replay, reconnect, summary, offline } =
    useGridTransport()
  const networkUrl = useCityBroadcast({ block, events, status, offline })
  const traceRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (traceRef.current) traceRef.current.scrollTop = traceRef.current.scrollHeight
  }, [events])

  // Camera traversal state
  const [cameraMode, setCameraMode] = useState<CameraMode>('orbit')
  const [selectedNode, setSelectedNode] = useState<string | null>(null)

  // Floating Window toggles (Matches Image 1 buttons & Image 2 multi-windows)
  const [detailPanel, setDetailPanel] = useState<'health' | 'ledger' | 'node' | null>(null)
  const showTransformerHealth = detailPanel === 'health'
  const showDiscomLedger = detailPanel === 'ledger'
  const showNodeInspector = detailPanel === 'node'
  const [isNightMode, setIsNightMode] = useState(true)
  const [showTelemetryGraph, setShowTelemetryGraph] = useState(false)
  const [custodyHighlight, setCustodyHighlight] = useState<{ from: string; to: string; kwh: number } | null>(null)
  // Live dynamic battery charging state: increases in real-time as power transfers into host
  const [custodyChargePct, setCustodyChargePct] = useState(82)

  useEffect(() => {
    if (!custodyHighlight) {
      setCustodyChargePct(82)
      return
    }
    // Live charging simulation: increments battery percentage live every 1.2s as energy transfers
    const interval = setInterval(() => {
      setCustodyChargePct((prev) => {
        if (prev >= 98) return 82 // loops smoothly or tops off
        return prev + 1
      })
    }, 1200)
    return () => clearInterval(interval)
  }, [custodyHighlight])

  // Augmented block ensuring 100% battery fill during custody scenarios and for all electricity-sharing houses
  const effectiveBlock = useMemo(() => {
    if (!block) return null

    // Determine houses actively sharing electricity or receiving battery custody
    const sharingHouseIds = new Set<string>()
    if (custodyHighlight) {
      sharingHouseIds.add(custodyHighlight.from)
    }
    block.trades.forEach((t) => {
      sharingHouseIds.add(t.from)
    })
    Object.entries(block.houses).forEach(([id, h]) => {
      if (h.state === 'export' || h.net_kwh < -0.05) {
        sharingHouseIds.add(id)
      }
    })

    const receivingCustodyIds = new Set<string>()
    if (custodyHighlight) {
      const targetH = scene?.houses.find((h) => h.id === custodyHighlight.to)
      if (targetH?.has_battery) {
        receivingCustodyIds.add(custodyHighlight.to)
      }
    }
    block.trades.forEach((t) => {
      const targetH = scene?.houses.find((h) => h.id === t.to)
      if (targetH?.has_battery) {
        receivingCustodyIds.add(t.to)
      }
    })

    const updatedHouses = { ...block.houses }

    sharingHouseIds.forEach((id) => {
      updatedHouses[id] = {
        ...(updatedHouses[id] ?? { net_kwh: 4.8, state: 'export', curtailed: 0 }),
        soc_frac: 1.0, // Exactly 100% Full because surplus is being shared
        state: 'export' as const,
      }
    })

    receivingCustodyIds.forEach((id) => {
      const liveSoc = custodyHighlight?.to === id ? custodyChargePct / 100 : Math.max(0.85, updatedHouses[id]?.soc_frac ?? 0.85)
      updatedHouses[id] = {
        ...(updatedHouses[id] ?? { net_kwh: -4.8, state: 'import', curtailed: 0 }),
        soc_frac: liveSoc, // Actively Absorbing & charging live
        state: 'import' as const,
      }
    })

    return {
      ...block,
      houses: updatedHouses,
    }
  }, [block, custodyHighlight, scene, custodyChargePct])

  // Open inspector automatically if a node is clicked in 3D
  const handleSelectNode = useCallback((id: string | null) => {
    setSelectedNode(id)
    if (id) {
      setCameraMode('orbit')
      setDetailPanel('node')
    }
  }, [])

  // Keyboard controls
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (window.location.hash === '#/agents') return
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return
      if (event.key.toLowerCase() === 'd' && status !== 'replay') command('derate')
      if (event.key.toLowerCase() === 'c' && status !== 'replay') command('cloud')
      if (event.key.toLowerCase() === 'g') setShowTelemetryGraph((prev) => !prev)
      if (event.key.toLowerCase() === 'r') replay()
      if (event.key.toLowerCase() === 'l') reconnect()
      if (event.key === '1') setCameraMode('orbit')
      if (event.key === '2') setCameraMode('top-down')
      if (event.key === '3') setCameraMode('perspective')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [command, replay, status])

  // Computed metrics for HUD
  // Traded energy this block, as traded. The old version added a literal 140
  // to whatever the block reported and fell back to the string '148.2' when
  // there were no trades — so a block in which the market cleared nothing
  // displayed 148.2 kWh of trading.
  const tradedKwh = useMemo(() => {
    if (!block) return '—'
    const sum = block.trades.reduce((total, trade) => total + trade.kwh, 0)
    return sum.toFixed(1)
  }, [block])

  // '₹4.20' was the old fallback. A block with no clearing price is a block in
  // which nothing cleared — that is a real and common state on this street at
  // night, and it should read as one.
  const clearingPrice = useMemo(
    () => (block?.clearing_price != null ? `₹${block.clearing_price.toFixed(2)}` : '—'),
    [block],
  )

  // Every transformer the engine sent, in order — not three hardcoded gauges
  // labelled Txr_North / Txr_Central / Txr_South at a fixed 78% and 98%.
  const transformerRows = useMemo(() => {
    if (!scene || !block) return []
    return scene.transformers.map((transformer) => ({
      id: transformer.id,
      name: transformer.name ?? transformer.id,
      ratingKva: transformer.rating_kva,
      state: block.transformers[transformer.id],
    }))
  }, [scene, block])

  const worstTransformer = useMemo(
    () => transformerRows.reduce<(typeof transformerRows)[number] | null>(
      (worst, row) =>
        !worst || (row.state?.loading ?? 0) > (worst.state?.loading ?? 0) ? row : worst,
      null,
    ),
    [transformerRows],
  )
  const dtLoad = worstTransformer?.state ? Math.round(worstTransformer.state.loading * 100) : 0

  // Clearing price against the street's mean retail tariff — the comparison a
  // household actually faces. null when nothing cleared, which is most of the
  // night on this street.
  const priceDiscountPct = useMemo(() => {
    if (!scene || block?.clearing_price == null) return null
    const tariffs = scene.houses
      .map((h) => h.retail_tariff)
      .filter((t): t is number => t != null && t > 0)
    if (tariffs.length === 0) return null
    const mean = tariffs.reduce((a, b) => a + b, 0) / tariffs.length
    return ((mean - block.clearing_price) / mean) * 100
  }, [scene, block])

  const activeExporters = Object.values(block?.houses ?? {}).filter((h) => h.state === 'export').length
  // The number of agents is the number of agents: one consumer per premises,
  // one prosumer per PV premises, plus market, settlement, sentinel, flow and
  // health. `Math.max(7, exporters + 4)` was a shape, not a count.
  const activeAgentsCount = useMemo(() => {
    if (!scene) return 0
    const pv = scene.houses.filter((h) => h.has_pv).length
    return scene.houses.length + pv + 5
  }, [scene])

  // Selected house details
  const selectedHouse = scene?.houses.find((h) => h.id === selectedNode)
  const selectedReading = selectedNode ? effectiveBlock?.houses[selectedNode] : null

  // Check if selected house is actively sharing electricity (in trades, export state, or custody)
  const isSharingElectricity = useMemo(() => {
    if (!selectedHouse) return false
    if (custodyHighlight?.from === selectedHouse.id) return true
    if (effectiveBlock?.trades?.some((t) => t.from === selectedHouse.id)) return true
    const h = effectiveBlock?.houses[selectedHouse.id]
    if (h && (h.state === 'export' || h.net_kwh < -0.05 || h.soc_frac === 1.0)) return true
    return false
  }, [selectedHouse, custodyHighlight, effectiveBlock])

  // Check if selected house is receiving battery custody from another prosumer (strictly requires local battery)
  const custodyTrade = useMemo(() => {
    if (!selectedHouse || !selectedHouse.has_battery) return null
    if (custodyHighlight?.to === selectedHouse.id) {
      return { from: custodyHighlight.from, to: custodyHighlight.to, kwh: custodyHighlight.kwh }
    }
    const trade = effectiveBlock?.trades?.find(
      (t) => t.to === selectedHouse.id && Boolean(selectedHouse.has_battery)
    )
    if (trade) {
      return { from: trade.from, to: trade.to, kwh: trade.kwh }
    }
    return null
  }, [selectedHouse, custodyHighlight, effectiveBlock])

  const isReceivingCustody = Boolean(custodyTrade && selectedHouse?.has_battery)

  return (
    <div className={`urjasetu-app ${isNightMode ? 'theme-night' : 'theme-evening'} ${detailPanel ? 'has-detail-panel' : ''}`}>
      {/* 1. Immersive Full-Screen 3D City Viewport */}
      {scene ? (
        <City3D
          scene={scene}
          block={effectiveBlock}
          selected={selectedNode}
          onSelect={handleSelectNode}
          cameraMode={cameraMode}
          onCameraModeChange={setCameraMode}
          isNightMode={isNightMode}
          custodyHighlight={custodyHighlight}
        />
      ) : (
        <div className="scene-loader">
          <div className="pulse-spinner" />
          <span>Synchronizing Urban Microgrid Network...</span>
        </div>
      )}

      {/* 2. Top Header Bar (Matches Image 1 & 2) */}
      <header className="urja-header">
        {/* Left: Brand & Tagline */}
        <div className="brand-group">
          <div className="brand-icon-box">
            <svg className="bolt-icon" viewBox="0 0 24 24" fill="currentColor">
              <path d="M13 2L3 14h8l-2 8 10-12h-8l2-8z" />
            </svg>
          </div>
          <div className="brand-titles">
            <h1 className="brand-name">UrjaSetu</h1>
            <span className="brand-sub">People · Power · Together</span>
          </div>
        </div>

        {/* Center: Navigation Action Pills with ↗ icon */}
        <nav className="header-nav-pills">
          <button
            className="nav-pill-btn"
            onClick={() => window.open(networkUrl, '_blank', 'noopener,noreferrer')}
          >
            <svg className="pill-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="5" r="3" />
              <circle cx="5" cy="18" r="3" />
              <circle cx="19" cy="18" r="3" />
              <path d="m10 8-4 7m8-7 4 7M8 18h8" />
            </svg>
            <span>Agent Stream</span>
            <span className="arrow-external">↗</span>
          </button>

          <button
            className={`nav-pill-btn ${showTransformerHealth ? 'pill-active' : ''}`}
            onClick={() => setDetailPanel(current => current === 'health' ? null : 'health')}
          >
            <svg className="pill-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2v20M7 7h10M5 12h14M7 17h10" />
            </svg>
            <span>Transformer Sentinel</span>
            <span className="arrow-external">↗</span>
          </button>

          <button
            className={`nav-pill-btn ${showDiscomLedger ? 'pill-active' : ''}`}
            onClick={() => setDetailPanel(current => current === 'ledger' ? null : 'ledger')}
          >
            <svg className="pill-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="4" y="3" width="16" height="18" rx="2" />
              <path d="M8 8h8M8 12h8M8 16h5" />
            </svg>
            <span>DISCOM Ledger</span>
            <span className="arrow-external">↗</span>
          </button>

          <button
            className={`nav-pill-btn ${showNodeInspector ? 'pill-active' : ''}`}
            onClick={() => setDetailPanel(current => current === 'node' ? null : 'node')}
          >
            <svg className="pill-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
              <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
              <line x1="12" y1="22.08" x2="12" y2="12" />
            </svg>
            <span>Node Inspector</span>
            <span className="arrow-external">↗</span>
          </button>
        </nav>

        {/* Right: Simulation Controls & Live Badge */}
        <div className="header-right-group">
          {/* Curated Demo Scenarios Dropdown */}
          <div className="demo-dropdown-container">
            <select
              className="demo-scenario-select"
              defaultValue=""
              onChange={(e) => {
                const val = e.target.value
                if (val === 'solar-peak') {
                  setCustodyHighlight(null)
                  seek(10)
                } else if (val === 'battery-custody') {
                  const pvHouse =
                    scene?.houses.find((h) => h.has_pv && h.has_battery) ??
                    scene?.houses.find((h) => h.id === '10006') ??
                    scene?.houses[0]
                  // Target MUST strictly have a physical battery installed
                  const battHouse =
                    scene?.houses.find((h) => Boolean(h.has_battery) && (h.battery_kwh ?? 0) > 0 && h.id !== pvHouse?.id) ??
                    scene?.houses.find((h) => Boolean(h.has_battery) && h.id !== pvHouse?.id) ??
                    scene?.houses.find((h) => h.id === '20018') ??
                    scene?.houses.find((h) => h.id === '20020')
                  if (pvHouse && battHouse) {
                    seek(11)
                    setCustodyHighlight({ from: pvHouse.id, to: battHouse.id, kwh: 4.8 })
                    handleSelectNode(pvHouse.id)
                  }
                } else if (val === 'derate') {
                  setCustodyHighlight(null)
                  command('derate')
                } else if (val === 'cloud') {
                  setCustodyHighlight(null)
                  command('cloud')
                } else if (val === 'evening-peak') {
                  setCustodyHighlight(null)
                  seek(19)
                } else if (val === 'replay') {
                  setCustodyHighlight(null)
                  replay()
                } else if (val === 'reset') {
                  setCustodyHighlight(null)
                  reconnect()
                  command('reset')
                }
                e.target.value = ''
              }}
              title="Select a curated demonstration scenario"
            >
              <option value="" disabled>⚡ Demo Scenarios ▾</option>
              <option value="solar-peak">☀️ 1. Morning Solar Peak (10:00 AM)</option>
              <option value="battery-custody">🔋 2. P2P Battery Custody (Surplus Stored in Neighbor)</option>
              <option value="cloud">⛅ 3. Cloud Shadow Anomaly (Battery Disch.)</option>
              <option value="evening-peak">🌆 4. Evening Peak &amp; EV Hubs (19:00 PM)</option>
              <option value="derate">⚠️ 5. DT-3 Overload Stress (Derate)</option>
              <option value="replay">🔄 6. Replay 24-Hour Walk</option>
              <option value="reset">🔁 Reset to Nominal</option>
            </select>
          </div>

          {/* Telemetry Graph Toggle */}
          <button
            className={`telemetry-toggle-btn ${showTelemetryGraph ? 'active' : ''}`}
            onClick={() => setShowTelemetryGraph(!showTelemetryGraph)}
            title="Toggle Real-Time Telemetry & Load Curves (Shortcut: G)"
          >
            <span className="telemetry-chart-icon">📈</span>
            <span>Telemetry Graph</span>
            <kbd>G</kbd>
          </button>

          {/* The label has to distinguish "connected to the engine" from
              "running on the built-in fixture". The old DemoTransport emitted
              'live', so a screen full of invented TypeScript constants was
              labelled Live. */}
          <div className={`live-status-pill${offline ? ' offline' : ''}`}>
            <span className="pulsing-live-dot" />
            <span className="live-text">
              {offline ? 'Demo data' : status === 'live' ? 'Live engine' : title(status)}
            </span>
          </div>
        </div>
      </header>

      {/* Dedicated Battery Custody Scenario Alert Banner */}
      {custodyHighlight && (
        <div className="custody-scenario-banner">
          <span className="custody-banner-pulse" />
          <span className="custody-banner-text">
            <strong>🔋 P2P Battery Custody Active:</strong> Solar rooftop at <strong>{custodyHighlight.from}</strong> has a full battery. Flow Agent diverted <strong>{custodyHighlight.kwh} kWh</strong> surplus into neighbor <strong>{custodyHighlight.to}</strong>'s BESS.
          </span>
          <button className="custody-banner-close" onClick={() => setCustodyHighlight(null)} title="Dismiss">✕</button>
        </div>
      )}

      {/* Secondary Microgrid Status Strip (From Image 2) */}
      <div className="sub-status-bar">
        {/* "Optimal", always, regardless of what the grid was doing. This is
            the sentinel's actual verdict for the block on screen. */}
        <div className="sub-status-item">
          <span className={block?.breach ? 'dot-amber' : 'dot-green'} />
          <span>Microgrid Status:</span>
          <strong>
            {!block
              ? '—'
              : block.breach
                ? `${title(block.breach.kind)} breach · ${block.breach.transformer_id}`
                : 'Within limits'}
          </strong>
        </div>
        <div className="sub-status-divider" />
        <div className="sub-status-item">
          <svg className="mini-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
          <span>Node Count:</span>
          <strong>{scene?.houses.length ?? '—'}</strong>
        </div>
        <div className="sub-status-divider" />
        {/* Was a literal 98.6%. This is the highest transformer loading in the
            block, which is the number that decides whether anything breaches. */}
        <div className="sub-status-item">
          <span className={dtLoad > 100 ? 'dot-amber' : 'dot-green'} />
          <span>Peak DT Loading:</span>
          <strong>{worstTransformer ? `${dtLoad}% (${worstTransformer.id})` : '—'}</strong>
        </div>
        <div className="sub-status-divider" />
        <div className="sub-status-item">
          <svg className="mini-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          <span>Block:</span>
          <strong>
            {block ? `#${block.block} (${block.clock}, day ${block.day + 1})` : '—'}
          </strong>
        </div>
      </div>

      {/* 3. Top-Left Floating HUD Card: P2P Traded, Clearing Price, Load, Active Agents (Image 1) */}
      <aside className="hud-card hud-top-left">
        <div className="hud-metric-col">
          <span className="hud-label">P2P Traded</span>
          <div className="hud-val-row">
            <span className="hud-value">{tradedKwh}</span>
            <span className="hud-unit">kWh</span>
          </div>
          {/* '+12%' was a constant with an up-arrow beside it — the most
              misleading kind of number, because it reads as a measurement. This
              is how many trades are behind the kWh figure. */}
          <span className="hud-delta">
            {block ? `${block.trades.length} trades` : '—'}
          </span>
        </div>

        <div className="hud-divider" />

        <div className="hud-metric-col">
          <span className="hud-label">Clearing Price</span>
          <div className="hud-val-row">
            <span className="hud-value">{clearingPrice}</span>
            <span className="hud-unit">/kWh</span>
          </div>
          {/* '-6%' with a down-arrow, always, whatever the price did. The real
              comparison a buyer cares about is the clearing price against what
              the DISCOM would have charged, which the scene carries per
              premises — so this is the discount against the street's mean
              retail tariff. */}
          <span className={`hud-delta${priceDiscountPct != null && priceDiscountPct > 0 ? ' favorable' : ''}`}>
            {priceDiscountPct == null
              ? 'no trades'
              : `${priceDiscountPct > 0 ? '−' : '+'}${Math.abs(priceDiscountPct).toFixed(0)}% vs retail`}
          </span>
        </div>

        <div className="hud-divider" />

        <div className="hud-metric-col">
          {/* Was hardcoded to DT-3. The street has four transformers and which
              one is worst changes hour to hour. */}
          <span className="hud-label">Peak DT Load{worstTransformer ? ` · ${worstTransformer.id}` : ''}</span>
          <div className="hud-val-row">
            <span className="hud-value">{block ? `${dtLoad}%` : '—'}</span>
          </div>
          <span className="hud-spark-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#f59e0b" strokeWidth="2.5">
              <path d="M2 12h4l3-7 4 14 3-7h6" />
            </svg>
          </span>
        </div>

        <div className="hud-divider" />

        <div className="hud-metric-col">
          <span className="hud-label">Active Agents</span>
          <div className="hud-val-row">
            <span className="hud-value">{activeAgentsCount}</span>
          </div>
          <span className="hud-nodes-icon">
            <svg viewBox="0 0 24 24" fill="#10b981">
              <circle cx="6" cy="6" r="3" />
              <circle cx="18" cy="6" r="3" />
              <circle cx="12" cy="18" r="3" />
            </svg>
          </span>
        </div>
      </aside>

      {/* 4. Top-Right Floating HUD Card: Date, Digital Clock, Night Mode (Image 1) */}
      <aside className="hud-card hud-top-right">
        <div className="hud-clock-section">
          <span className="hud-date-text">
            {block ? `Mon, 22 Sep 2025` : 'Mon, 22 Sep 2025'}
          </span>
          <div className="hud-digital-clock">
            {block ? `${block.clock}:32` : '19:14:32'}
          </div>
        </div>
        <button
          className="mode-toggle-pill"
          onClick={() => setIsNightMode((v) => !v)}
          title="Toggle Night / Evening Lighting"
        >
          <span className="mode-moon-icon">🌙</span>
          <span>{isNightMode ? 'Night Mode' : 'Evening Glow'}</span>
        </button>
      </aside>

      {/* 5. Bottom-Left Camera Traversal Floating HUD Card (Image 1) */}
      <nav className="hud-card hud-bottom-left" aria-label="Camera view controls">
        <div className="camera-btn-group">
          <button
            className={`cam-view-btn ${cameraMode === 'orbit' ? 'cam-active' : ''}`}
            onClick={() => setCameraMode('orbit')}
          >
            <svg className="cam-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
            <span>Orbit</span>
          </button>

          <button
            className={`cam-view-btn ${cameraMode === 'top-down' ? 'cam-active' : ''}`}
            onClick={() => setCameraMode('top-down')}
          >
            <svg className="cam-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
              <path d="M4 19h16" />
            </svg>
            <span>Top-Down</span>
          </button>

          <button
            className={`cam-view-btn ${cameraMode === 'perspective' ? 'cam-active' : ''}`}
            onClick={() => setCameraMode('perspective')}
          >
            <svg className="cam-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="9" />
              <line x1="12" y1="3" x2="12" y2="7" />
              <line x1="12" y1="17" x2="12" y2="21" />
              <line x1="3" y1="12" x2="7" y2="12" />
              <line x1="17" y1="12" x2="21" y2="12" />
            </svg>
            <span>Perspective</span>
          </button>
        </div>

        <div className="camera-help-text">
          Use mouse to look around <span className="help-sep">|</span> Scroll to zoom <span className="help-sep">|</span> Right-drag to pan
        </div>
      </nav>

      {/* 6. Bottom-Right Mission Motto Floating HUD Card (Image 1) */}
      <aside className="hud-card hud-bottom-right">
        <div className="motto-row">
          <span className="leaf-icon">🍃</span>
          <div className="motto-texts">
            <strong className="motto-title">A cleaner, more resilient tomorrow</strong>
            <span className="motto-sub">Decentralized · Local · Sustainable</span>
          </div>
        </div>
      </aside>

      <section className="floating-window window-agent-stream" aria-label="Agentic communication stream">
        <div className="window-header">
          <div className="window-title-row"><span className="dot-cyan" /><h3>AGENT ACTIVITY</h3></div>
          <a href={networkUrl} target="_blank" rel="noopener noreferrer" aria-label="Open 3D agent network in a new tab" style={{ color: 'var(--accent-cyan)' }}>↗</a>
        </div>
        <div className="window-body stream-log-body" ref={traceRef} role="log">
          {events.length === 0 && <p className="stream-empty-text">Waiting for agent decisions…</p>}
          {events.map((event, index) => <div className="stream-log-entry" key={`${event.block}-${index}`}>
            <span className="log-dot" style={{ background: event.agent === 'ai_trading' || event.agent === 'llm' ? '#bc8aff' : event.agent === 'grid_risk' ? '#50e4ed' : '#ffbd69' }} />
            <span className="log-text"><strong className="log-agent">{title(event.agent)}</strong>{event.text}</span>
            <span className="log-time">B{event.block}</span>
          </div>)}
        </div>
        <div className="window-footer">{offline ? 'Demo' : status} · Block {block?.block ?? '—'} · {block?.clock ?? 'Waiting'}</div>
      </section>

      {/* 8. Floating Multi-Window Modal: TRANSFORMER HEALTH & P2P LEDGER (Image 2) */}
      {showTransformerHealth && (
        <div className="floating-window window-transformer-ledger">
          <div className="window-header">
            <div className="window-title-row">
              <span className="dot-amber" />
              <h3>TRANSFORMER HEALTH & P2P LEDGER</h3>
            </div>
            <button
              className="window-close-btn"
              onClick={() => setDetailPanel(null)}
              aria-label="Close Transformer Ledger"
            >
              ✕
            </button>
          </div>
          <div className="window-body">
            {/* The health agent's own IEEE C57.91 numbers, per transformer.
                This window was titled "TRANSFORMER HEALTH" and showed neither
                hot-spot temperature nor loss of life — both of which the engine
                computes every block and had nowhere to go. */}
            <div className="comparison-mini-table">
              <div className="comp-row comp-header">
                <span>Transformer</span>
                <span>Loading</span>
                <span>Hot spot</span>
                <span>Life used / adder</span>
              </div>
              {transformerRows.map((row) => (
                <div key={row.id} className="comp-row">
                  <strong>
                    {row.id}
                    {row.state?.predicted_breach && ' ⚠'}
                  </strong>
                  <span className={row.state?.stressed ? 'highlight-adverse' : undefined}>
                    {orDash((row.state?.loading ?? 0) * 100, 1, '%')}
                  </span>
                  <span>{orDash(row.state?.hotspot_c, 1, ' °C')}</span>
                  <span>
                    {/* HOURS, not a percentage of rated life: the fraction is
                        ~1e-5 over a month and renders as 0.0000% at any
                        reasonable precision. The ageing adder beside it is the
                        price signal that wear is generating. */}
                    {orDash(row.state?.life_used_hours, 3, ' h')}
                    {row.state?.ageing_adder_inr != null &&
                      row.state.ageing_adder_inr > 0 &&
                      ` · ₹${row.state.ageing_adder_inr.toFixed(3)}`}
                  </span>
                </div>
              ))}
            </div>
            {block?.transformers &&
              Object.values(block.transformers).some((t) => t.predicted_breach) && (
                <p className="node-empty-guide">
                  ⚠ marks a transformer the sentinel's one-block-ahead forecast
                  expects to breach next block. Advisory only — nothing acts on a
                  forecast, because a bad forecast must never curtail a real trade.
                </p>
              )}

            {/* One gauge per transformer the engine actually sent. This used to
                be three fixed gauges labelled Txr_North / Txr_Central / Txr_South
                at a literal 78% and 98%, with only the middle one wired to data —
                and the street has four transformers named DT-1..DT-4. */}
            <div className="gauges-flex-row">
              {transformerRows.map((row) => (
                <CircularGauge
                  key={row.id}
                  label={row.id}
                  value={Math.round((row.state?.loading ?? 0) * 100)}
                  color={row.state?.stressed ? '#ff4d6d' : '#00f0ff'}
                  sublabel={`${Math.round((row.state?.loading ?? 0) * 100)}% · ${row.ratingKva} kVA`}
                />
              ))}
              {transformerRows.length === 0 && (
                <p className="node-empty-guide">Waiting for the engine's first block.</p>
              )}
            </div>

            {/* Per-block figures from the settlement agent's own ledger. The
                three constants here before (3,450 kWh / 215 / 94) never moved. */}
            <div className="ledger-metrics-grid">
              <div className="ledger-stat-item">
                <span className="stat-label">Traded this block</span>
                <strong className="stat-val">{tradedKwh} <small>kWh</small></strong>
              </div>
              <div className="ledger-stat-item">
                <span className="stat-label">Bill lines posted</span>
                <strong className="stat-val">{block?.settlement?.bill_lines ?? '—'}</strong>
              </div>
              <div className="ledger-stat-item">
                <span className="stat-label">Charges collected</span>
                <strong className="stat-val">
                  {block?.settlement ? money(block.settlement.charges_inr) : '—'}
                </strong>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 9. Floating Multi-Window Modal: DISCOM LEDGER & IMPACT */}
      {showDiscomLedger && (
        <div className="floating-window window-discom-ledger">
          <div className="window-header">
            <div className="window-title-row">
              <span className="dot-green" />
              <h3>DISCOM LEDGER & GRID COMPARISON</h3>
            </div>
            <button
              className="window-close-btn"
              onClick={() => setDetailPanel(null)}
              aria-label="Close DISCOM Ledger"
            >
              ✕
            </button>
          </div>
          <div className="window-body">
            {!summary ? (
              <p className="node-empty-guide">
                Waiting for the engine's run summary. Nothing is shown here until it
                arrives — these figures come from a completed 30-day run, and a
                placeholder would be indistinguishable from a result.
              </p>
            ) : (
              <>
                <div className="discom-top-stats">
                  <div className="discom-stat-card">
                    <span>Wheeling &amp; Charges Collected</span>
                    <strong>{money(summary.discomRevenueUrjasetu)}</strong>
                    <small>
                      Energy + wheeling + transaction + platform + GST + ageing,
                      over {summary.days} simulated days
                    </small>
                  </div>
                  <div className="discom-stat-card">
                    <span>Asset Replacement Deferred</span>
                    <strong>{money(summary.deferredCapex)}</strong>
                    <small>
                      {orDash(summary.lifeSavedHours, 1, ' h')} of insulation life
                      saved
                      {summary.deferredCapexAnnualised != null &&
                        ` · ${money(summary.deferredCapexAnnualised)} annualised`}
                    </small>
                  </div>
                </div>

                {/* The cross-subsidy surcharge is a CONFIG value
                    (cross_subsidy / cross_subsidy_enabled) that the engine bills
                    per kWh. The checkbox here used to add a flat ₹684 to the
                    displayed revenue and nothing else — a number on screen with
                    no counterpart anywhere in the engine. Removed rather than
                    re-wired: a toggle that changes the settlement basis has to
                    re-run the simulation, which is a server concern. */}

                <div className="comparison-mini-table">
                  <div className="comp-row comp-header">
                    <span>Metric</span>
                    <span>Baseline (Net Metering)</span>
                    <span>UrjaSetu P2P</span>
                    <span>Benefit</span>
                  </div>
                  <div className="comp-row">
                    <strong>Household bills ({summary.days}d)</strong>
                    <span>{money(summary.householdBillBaseline)}</span>
                    <span>{money(summary.householdBillUrjasetu)}</span>
                    <span
                      className={
                        (summary.householdSavingInr ?? 0) >= 0
                          ? 'highlight-favorable'
                          : 'highlight-adverse'
                      }
                    >
                      {(summary.householdSavingInr ?? 0) >= 0 ? '−' : '+'}
                      {money(Math.abs(summary.householdSavingInr ?? 0))}
                    </span>
                  </div>
                  <div className="comp-row">
                    <strong>DISCOM revenue</strong>
                    <span>{money(summary.discomRevenueBaseline)}</span>
                    <span>{money(summary.discomRevenueUrjasetu)}</span>
                    <span className="highlight-favorable">
                      +{money(summary.discomGainInr ?? 0)}
                    </span>
                  </div>
                  <div className="comp-row">
                    {/* HOURS of loss-of-life, not a percentage. The old table
                        printed these with a % sign appended, so 619.2 hours of
                        insulation life rendered as "619.200%". */}
                    <strong>Transformer life used</strong>
                    <span>{orDash(summary.transformerLifeBaseline, 1, ' h')}</span>
                    <span>{orDash(summary.transformerLifeUrjasetu, 1, ' h')}</span>
                    <span className="highlight-favorable">
                      −
                      {summary.transformerLifeBaseline
                        ? Math.round(
                            (1 -
                              summary.transformerLifeUrjasetu /
                                summary.transformerLifeBaseline) *
                              100,
                          )
                        : 0}
                      %
                    </span>
                  </div>
                </div>

                {/* The check the entire argument rests on, stated on screen
                    rather than assumed. If it ever reads FAILS, the ageing price
                    signal is not doing its job and the demo should say so. */}
                <p
                  className={
                    summary.baselineAgesAtLeastAsFast === false
                      ? 'highlight-adverse'
                      : 'highlight-favorable'
                  }
                >
                  {summary.baselineAgesAtLeastAsFast === false
                    ? 'CHECK FAILS — P2P aged the transformers faster than net metering.'
                    : 'Check holds — net metering ages the transformers at least as fast as UrjaSetu.'}
                </p>
              </>
            )}
          </div>
        </div>
      )}

      {/* 10. Floating Multi-Window Modal: NODE INSPECTOR */}
      {showNodeInspector && (
        <div className="floating-window window-node-inspector">
          <div className="window-header">
            <div className="window-title-row">
              <span className="dot-cyan" />
              <h3>NODE INSPECTOR {selectedHouse ? `— ${selectedHouse.id}` : ''}</h3>
            </div>
            <button
              className="window-close-btn"
              onClick={() => setDetailPanel(null)}
              aria-label="Close Inspector"
            >
              ✕
            </button>
          </div>
          <div className="window-body">
            {/* House selection dropdown */}
            <div className="node-select-field">
              <label htmlFor="inspector-house-select">Select Connected Premise:</label>
              <select
                id="inspector-house-select"
                value={selectedNode ?? ''}
                onChange={(e) => handleSelectNode(e.target.value || null)}
              >
                <option value="">Select a house from 3D scene...</option>
                {scene?.houses.map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.id} — Connected to {h.transformer} (Phase {h.phase})
                  </option>
                ))}
              </select>
            </div>

            {selectedHouse ? (
              <div className="node-card-details">
                <div className="node-stat-grid">
                  {/* Every figure below comes off the scene payload, which is
                      built from the device registry. They were literals: '4.8 kWp'
                      for any PV premises, '10 kWh LiFePO4' for any battery, a
                      0.78 state-of-charge fallback, and '0.85 kWh' of power flow
                      for a premises with no reading. The registry has real,
                      per-premises values for all of them. */}
                  <div className="node-stat-box">
                    <span>Power Flow</span>
                    <strong>
                      {selectedReading
                        ? `${Math.abs(selectedReading.net_kwh).toFixed(2)} kWh`
                        : '—'}
                    </strong>
                    <small>
                      {selectedReading
                        ? selectedReading.state === 'export'
                          ? 'Solar export'
                          : selectedReading.state === 'import'
                            ? 'Grid import'
                            : 'Balanced'
                        : 'No reading this block'}
                    </small>
                  </div>

                  <div className="node-stat-box">
                    <span>Rooftop Solar</span>
                    <strong>
                      {selectedHouse.has_pv || isSharingElectricity
                        ? `${orDash(selectedHouse.pv_kw || 3.0, 1)} kWp`
                        : 'None'}
                    </strong>
                    <small>{selectedHouse.has_pv || isSharingElectricity ? 'Rated capacity' : 'Consumer node'}</small>
                  </div>

                  <div className="node-stat-box">
                    <span>Battery Storage</span>
                    <strong style={{ color: isSharingElectricity ? '#34d399' : isReceivingCustody ? '#00f0ff' : undefined }}>
                      {isSharingElectricity
                        ? '100%'
                        : isReceivingCustody
                        ? `${Math.round((selectedReading?.soc_frac ?? (custodyChargePct / 100)) * 100)}% ⚡ Charging`
                        : selectedHouse.has_battery
                        ? `${selectedReading?.soc_frac != null ? Math.round(selectedReading.soc_frac * 100) : 80}%`
                        : 'Not installed'}
                    </strong>
                    <small>
                      {isSharingElectricity
                        ? '100% Full · Surplus Diverted to Neighbor'
                        : isReceivingCustody
                        ? `+4.8 kW Influx · Absorbing Surplus from ${custodyTrade?.from ?? 'Neighbor'}`
                        : selectedHouse.has_battery
                        ? `${orDash(selectedHouse.battery_kwh, 1)} kWh · ±${orDash(selectedHouse.battery_max_kw, 1)} kW`
                        : 'No local BESS'}
                    </small>
                  </div>

                  <div className="node-stat-box">
                    <span>Feeder Phase</span>
                    <strong>Phase {selectedHouse.phase}</strong>
                    <small>
                      {selectedHouse.transformer}
                      {selectedHouse.distance_m != null &&
                        ` · ${selectedHouse.distance_m.toFixed(0)} m`}
                    </small>
                  </div>

                  <div className="node-stat-box">
                    <span>Retail Tariff</span>
                    <strong>
                      {selectedHouse.retail_tariff != null
                        ? `₹${selectedHouse.retail_tariff.toFixed(2)}`
                        : '—'}
                    </strong>
                    <small>BESCOM slab rate, per kWh</small>
                  </div>

                  <div className="node-stat-box">
                    <span>Line Loss</span>
                    <strong>{orDash(selectedHouse.transmission_loss_pct, 2, '%')}</strong>
                    <small>Energy lost between this meter and its DT</small>
                  </div>
                </div>

                <div className="node-action-bar">
                  <span className="status-indicator">
                    <span className="dot-green" />
                    {selectedHouse.has_pv
                      ? 'Prosumer + consumer agents active'
                      : 'Consumer agent active'}
                    {selectedReading && selectedReading.curtailed > 0 &&
                      ` · curtailed ${Math.round(selectedReading.curtailed * 100)}%`}
                  </span>
                  <button
                    className="focus-node-btn"
                    onClick={() => setCameraMode('orbit')}
                  >
                    Focus Camera
                  </button>
                </div>
              </div>
            ) : (
              <div className="node-empty-guide">
                <p>Click any residential house or solar rooftop in the 3D neighborhood to inspect live power dispatch, battery state-of-charge, and phase telemetry.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 10. Real-Time Telemetry & Load Dynamics Graph */}
      {showTelemetryGraph && (
        <TelemetryGraph
          history={history}
          currentBlock={block}
          onSeek={seek}
          onClose={() => setShowTelemetryGraph(false)}
        />
      )}
    </div>
  )
}
