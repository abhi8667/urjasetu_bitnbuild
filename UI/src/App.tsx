import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { City3D, type CameraMode } from './City3D'
import { createDemoRun } from './demoFixture'
import { DemoTransport, ReplayTransport } from './transport'
import type { BlockPayload, EventPayload, RunSummary, ScenePayload, Transport, TransportStatus } from './types'

const money = (value: number) => `₹${Math.round(value).toLocaleString('en-IN')}`
const title = (value: string) => value.charAt(0).toUpperCase() + value.slice(1)

function useGridTransport() {
  const liveRun = useMemo(createDemoRun, [])
  const transportRef = useRef<Transport | null>(null)
  const disconnectRef = useRef<(() => void) | null>(null)
  const [scene, setScene] = useState<ScenePayload | null>(null)
  const [block, setBlock] = useState<BlockPayload | null>(null)
  const [status, setStatus] = useState<TransportStatus>('live')
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
      }),
      transport.onEvent((event) => setEvents((current) => [...current, event].slice(-50))),
      transport.onStatus(setStatus),
    ]
    transport.start()
    disconnectRef.current = () => {
      offs.forEach((off) => off())
      transport.stop()
    }
    return () => disconnectRef.current?.()
  }, [])

  useEffect(() => connect(new DemoTransport(liveRun)), [connect, liveRun])

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

  const replay = useCallback(() => {
    setEvents([])
    connect(new ReplayTransport(createDemoRun()))
  }, [connect])

  return { scene, block, status, events, command, replay, summary: liveRun.summary }
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
  const { scene, block, status, events, command, replay, summary } = useGridTransport()

  // Camera traversal state
  const [cameraMode, setCameraMode] = useState<CameraMode>('orbit')
  const [selectedNode, setSelectedNode] = useState<string | null>(null)

  // Floating Window toggles (Matches Image 1 buttons & Image 2 multi-windows)
  const [showAgentStream, setShowAgentStream] = useState(false)
  const [showTransformerHealth, setShowTransformerHealth] = useState(false)
  const [showDiscomLedger, setShowDiscomLedger] = useState(false)
  const [showNodeInspector, setShowNodeInspector] = useState(false)
  const [isNightMode, setIsNightMode] = useState(true)
  const [subsidy, setSubsidy] = useState(false)

  const traceRef = useRef<HTMLDivElement>(null)

  // Scroll to bottom of agent stream
  useEffect(() => {
    if (traceRef.current) {
      traceRef.current.scrollTop = traceRef.current.scrollHeight
    }
  }, [events])

  // Open inspector automatically if a node is clicked in 3D
  const handleSelectNode = useCallback((id: string | null) => {
    setSelectedNode(id)
    if (id) {
      setShowNodeInspector(true)
    }
  }, [])

  // Keyboard controls
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return
      if (event.key.toLowerCase() === 'd' && status !== 'replay') command('derate')
      if (event.key.toLowerCase() === 'c' && status !== 'replay') command('cloud')
      if (event.key.toLowerCase() === 'r') replay()
      if (event.key === '1') setCameraMode('orbit')
      if (event.key === '2') setCameraMode('top-down')
      if (event.key === '3') setCameraMode('perspective')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [command, replay, status])

  // Computed metrics for HUD
  const tradedKwh = useMemo(() => {
    const sum = block?.trades.reduce((total, trade) => total + trade.kwh, 0) ?? 0
    return sum > 0 ? (140 + sum).toFixed(1) : '148.2'
  }, [block])

  const clearingPrice = useMemo(() => {
    return block?.clearing_price != null ? `₹${block.clearing_price.toFixed(2)}` : '₹4.20'
  }, [block])

  const centralTransformer = block?.transformers['DT-3']
  const dtLoad = centralTransformer ? Math.round(centralTransformer.loading * 100) : 72
  const activeExporters = Object.values(block?.houses ?? {}).filter((h) => h.state === 'export').length
  const activeAgentsCount = Math.max(7, activeExporters + 4)

  // Selected house details
  const selectedHouse = scene?.houses.find((h) => h.id === selectedNode)
  const selectedReading = selectedNode ? block?.houses[selectedNode] : null

  return (
    <div className={`urjasetu-app ${isNightMode ? 'theme-night' : 'theme-evening'}`}>
      {/* 1. Immersive Full-Screen 3D City Viewport */}
      {scene ? (
        <City3D
          scene={scene}
          block={block}
          selected={selectedNode}
          onSelect={handleSelectNode}
          cameraMode={cameraMode}
          onCameraModeChange={setCameraMode}
          isNightMode={isNightMode}
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
            className={`nav-pill-btn ${showAgentStream ? 'pill-active' : ''}`}
            onClick={() => setShowAgentStream((v) => !v)}
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
            onClick={() => setShowTransformerHealth((v) => !v)}
          >
            <svg className="pill-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2v20M7 7h10M5 12h14M7 17h10" />
            </svg>
            <span>Transformer Sentinel</span>
            <span className="arrow-external">↗</span>
          </button>

          <button
            className={`nav-pill-btn ${showDiscomLedger ? 'pill-active' : ''}`}
            onClick={() => setShowDiscomLedger((v) => !v)}
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
            onClick={() => setShowNodeInspector((v) => !v)}
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
          {/* Quick Simulation Trigger Buttons */}
          <div className="quick-sim-buttons">
            <button
              className="quick-action-btn"
              onClick={() => command('derate')}
              title="Derate central transformer DT-3 (Shortcut: D)"
            >
              Derate DT-3 <kbd>D</kbd>
            </button>
            <button
              className="quick-action-btn"
              onClick={() => command('cloud')}
              title="Simulate cloud bank over solar panels (Shortcut: C)"
            >
              Cloud Bank <kbd>C</kbd>
            </button>
            <button
              className="quick-action-btn"
              onClick={replay}
              title="Replay simulation block stream (Shortcut: R)"
            >
              Replay <kbd>R</kbd>
            </button>
          </div>

          <div className="live-status-pill">
            <span className="pulsing-live-dot" />
            <span className="live-text">{status === 'live' ? 'Live' : title(status)}</span>
          </div>
        </div>
      </header>

      {/* Secondary Microgrid Status Strip (From Image 2) */}
      <div className="sub-status-bar">
        <div className="sub-status-item">
          <span className="dot-green" />
          <span>Microgrid Status:</span>
          <strong>Optimal</strong>
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
          <strong>{scene?.houses.length ?? 14}</strong>
        </div>
        <div className="sub-status-divider" />
        <div className="sub-status-item">
          <span className="dot-amber" />
          <span>Grid Balance:</span>
          <strong>98.6%</strong>
        </div>
        <div className="sub-status-divider" />
        <div className="sub-status-item">
          <svg className="mini-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          <span>Block:</span>
          <strong>#{block?.block ?? 1} ({block?.clock ?? '19:14'})</strong>
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
          <span className="hud-delta positive">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="delta-arrow">
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
            +12%
          </span>
        </div>

        <div className="hud-divider" />

        <div className="hud-metric-col">
          <span className="hud-label">Clearing Price</span>
          <div className="hud-val-row">
            <span className="hud-value">{clearingPrice}</span>
            <span className="hud-unit">/kWh</span>
          </div>
          <span className="hud-delta favorable">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="delta-arrow">
              <path d="M12 5v14M19 12l-7 7-7-7" />
            </svg>
            -6%
          </span>
        </div>

        <div className="hud-divider" />

        <div className="hud-metric-col">
          <span className="hud-label">DT-3 Grid Load</span>
          <div className="hud-val-row">
            <span className="hud-value">{dtLoad}%</span>
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

      {/* 7. Floating Multi-Window Modal: AGENT ACTIVITY STREAM (Image 2) */}
      {showAgentStream && (
        <div className="floating-window window-agent-stream">
          <div className="window-header">
            <div className="window-title-row">
              <span className="dot-cyan" />
              <h3>AGENT ACTIVITY STREAM</h3>
            </div>
            <button
              className="window-close-btn"
              onClick={() => setShowAgentStream(false)}
              aria-label="Close Agent Stream"
            >
              ✕
            </button>
          </div>
          <div className="window-body stream-log-body" ref={traceRef}>
            {events.length === 0 ? (
              <div className="stream-empty-text">Listening for peer-to-peer agent broadcasts...</div>
            ) : (
              events.map((ev, i) => {
                const isProsumer = ev.agent.toLowerCase().includes('prosumer')
                const isSentinel = ev.agent.toLowerCase().includes('sentinel')
                const isFlow = ev.agent.toLowerCase().includes('flow')
                const dotColor = isProsumer ? 'dot-green' : isSentinel ? 'dot-amber' : 'dot-cyan'

                return (
                  <div key={`${ev.block}-${i}`} className="stream-log-entry">
                    <span className={`log-dot ${dotColor}`} />
                    <span className="log-text">
                      <strong className="log-agent">[{title(ev.agent)}_Agent]:</strong> {ev.text}
                    </span>
                    <span className="log-time">14:{((i * 4) % 60).toString().padStart(2, '0')}</span>
                  </div>
                )
              })
            )}
          </div>
          <div className="window-footer">
            <span>Live autonomous negotiations</span>
            <small>Block #{block?.block ?? '—'}</small>
          </div>
        </div>
      )}

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
              onClick={() => setShowTransformerHealth(false)}
              aria-label="Close Transformer Ledger"
            >
              ✕
            </button>
          </div>
          <div className="window-body">
            {/* Radial circular gauges (Txr_North 78%, Txr_Central 92%, Txr_Nerth 98%) */}
            <div className="gauges-flex-row">
              <CircularGauge label="Txr_North" value={78} color="#00f0ff" sublabel="78%" />
              <CircularGauge label="Txr_Central" value={dtLoad} color="#ffb703" sublabel={`${dtLoad}%`} />
              <CircularGauge label="Txr_South" value={98} color="#00f0ff" sublabel="98%" />
            </div>

            {/* Bottom summary metrics (Matches Image 2!) */}
            <div className="ledger-metrics-grid">
              <div className="ledger-stat-item">
                <span className="stat-label">Today's Volume</span>
                <strong className="stat-val">3,450 <small>kWh</small></strong>
              </div>
              <div className="ledger-stat-item">
                <span className="stat-label">P2P Settlements</span>
                <strong className="stat-val">215</strong>
              </div>
              <div className="ledger-stat-item">
                <span className="stat-label">Active Contracts</span>
                <strong className="stat-val">94</strong>
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
              onClick={() => setShowDiscomLedger(false)}
              aria-label="Close DISCOM Ledger"
            >
              ✕
            </button>
          </div>
          <div className="window-body">
            <div className="discom-top-stats">
              <div className="discom-stat-card">
                <span>Wheeling & Charges Collected</span>
                <strong>{money(summary.discomRevenueUrjasetu + (subsidy ? 684 : 0))}</strong>
                <small>Wheeling fees + clearing settlements</small>
              </div>
              <div className="discom-stat-card">
                <span>Asset Replacement Deferred</span>
                <strong>{money(summary.deferredCapex)}</strong>
                <small>Modeled transformer lifetime extension</small>
              </div>
            </div>

            <label className="subsidy-toggle-label">
              <input
                type="checkbox"
                checked={subsidy}
                onChange={(e) => setSubsidy(e.target.checked)}
              />
              <span>Apply Configured DISCOM Cross-Subsidy Surcharge (+₹684)</span>
            </label>

            {/* Comparison Table */}
            <div className="comparison-mini-table">
              <div className="comp-row comp-header">
                <span>Metric</span>
                <span>Baseline (Net Metering)</span>
                <span>UrjaSetu P2P</span>
                <span>Benefit</span>
              </div>
              <div className="comp-row">
                <strong>Household Monthly Bill</strong>
                <span>{money(summary.householdBillBaseline)}</span>
                <span>{money(summary.householdBillUrjasetu)}</span>
                <span className="highlight-favorable">−{money(summary.householdBillBaseline - summary.householdBillUrjasetu)}</span>
              </div>
              <div className="comp-row">
                <strong>DISCOM Revenue</strong>
                <span>{money(summary.discomRevenueBaseline)}</span>
                <span>{money(summary.discomRevenueUrjasetu)}</span>
                <span className="highlight-favorable">+{money(summary.discomRevenueUrjasetu - summary.discomRevenueBaseline)}</span>
              </div>
              <div className="comp-row">
                <strong>Transformer Degradation</strong>
                <span>{summary.transformerLifeBaseline.toFixed(3)}%</span>
                <span>{summary.transformerLifeUrjasetu.toFixed(3)}%</span>
                <span className="highlight-favorable">−{Math.round((1 - summary.transformerLifeUrjasetu / summary.transformerLifeBaseline) * 100)}%</span>
              </div>
            </div>
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
              onClick={() => setShowNodeInspector(false)}
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
                  <div className="node-stat-box">
                    <span>Power Flow</span>
                    <strong>
                      {selectedReading
                        ? `${Math.abs(selectedReading.net_kwh).toFixed(2)} kWh`
                        : '0.85 kWh'}
                    </strong>
                    <small>{selectedReading?.state === 'export' ? 'Solar Export' : 'Grid Import'}</small>
                  </div>

                  <div className="node-stat-box">
                    <span>Rooftop Solar</span>
                    <strong>{selectedHouse.has_pv ? '4.8 kWp' : 'None'}</strong>
                    <small>{selectedHouse.has_pv ? 'Active Generation' : 'Consumer Node'}</small>
                  </div>

                  <div className="node-stat-box">
                    <span>Battery Storage</span>
                    <strong>
                      {selectedHouse.has_battery
                        ? `${Math.round((selectedReading?.soc_frac ?? 0.78) * 100)}%`
                        : 'Not Installed'}
                    </strong>
                    <small>{selectedHouse.has_battery ? '10 kWh LiFePO4' : 'No local BESS'}</small>
                  </div>

                  <div className="node-stat-box">
                    <span>Feeder Phase</span>
                    <strong>Phase {selectedHouse.phase}</strong>
                    <small>Fed via {selectedHouse.transformer}</small>
                  </div>
                </div>

                <div className="node-action-bar">
                  <span className="status-indicator">
                    <span className="dot-green" /> Smart Agent Active
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
    </div>
  )
}
