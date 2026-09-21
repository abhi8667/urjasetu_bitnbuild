import React, { useMemo, useState } from 'react'
import type { BlockPayload } from './types'

interface TelemetryGraphProps {
  history: BlockPayload[]
  currentBlock: BlockPayload | null
  onSeek?: (block: number) => void
  onClose: () => void
}

const DT_COLORS: Record<string, string> = {
  'DT-1': '#06b6d4', // Cyan
  'DT-2': '#10b981', // Emerald
  'DT-3': '#f59e0b', // Amber (central / most active)
  'DT-4': '#a855f7', // Purple
}

/** Colour for a transformer the palette above does not name. The street has
 *  four transformers today, but the chart is fed from the engine's block
 *  payload and a hardcoded key list silently drops any fifth one. */
const FALLBACK_COLORS = ['#ec4899', '#38bdf8', '#84cc16', '#fb923c']
const colorFor = (id: string, index: number) =>
  DT_COLORS[id] ?? FALLBACK_COLORS[index % FALLBACK_COLORS.length]

export const TelemetryGraph: React.FC<TelemetryGraphProps> = ({
  history,
  currentBlock,
  onSeek,
  onClose,
}) => {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null)

  // Escape key closes modal
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  // Use history or fallback to at least 24 points based on the current run
  const dataPoints = history.length > 0 ? history : currentBlock ? [currentBlock] : []

  // Expanded chart dimensions with generous widescreen headroom
  const width = 1440
  const height = 380
  const padding = { top: 40, right: 74, bottom: 44, left: 68 }
  const plotWidth = width - padding.left - padding.right
  const plotHeight = height - padding.top - padding.bottom

  // Y-axis range for loading. 140% was a fixed ceiling, so a genuine 180%
  // overload — exactly the event this chart exists to show — drew as a flat
  // line pinned to the top border, indistinguishable from 140%. The axis grows
  // to fit the worst reading instead, in 20-point steps.
  const peakLoadingPct = dataPoints.reduce(
    (peak, bp) =>
      Object.values(bp.transformers ?? {}).reduce(
        (blockPeak, t) => Math.max(blockPeak, (t?.loading ?? 0) * 100),
        peak,
      ),
    0,
  )
  const maxY = Math.max(140, Math.ceil(peakLoadingPct / 20) * 20)
  const getY = (valPct: number) => padding.top + plotHeight - (Math.min(valPct, maxY) / maxY) * plotHeight
  const getX = (idx: number) =>
    dataPoints.length > 1
      ? padding.left + (idx / (dataPoints.length - 1)) * plotWidth
      : padding.left + plotWidth / 2

  // Limit line at 100%
  const y100 = getY(100)

  // Price range: 0 to 10 INR/kWh
  const maxPrice = 10
  const getPriceY = (price: number) =>
    padding.top + plotHeight - (Math.min(price, maxPrice) / maxPrice) * plotHeight

  // The transformers the engine actually sent, in stable order. A hardcoded
  // ['DT-1'..'DT-4'] draws a flat 0% line for any id the run does not have and
  // silently omits any it does — the same class of bug the HUD gauges had.
  const dtKeys = useMemo(() => {
    const seen = new Set<string>()
    dataPoints.forEach((bp) => Object.keys(bp.transformers ?? {}).forEach((id) => seen.add(id)))
    return [...seen].sort()
  }, [dataPoints])

  // A transformer missing from one block is a gap, not a zero. Each run of
  // consecutive readings becomes its own subpath so the line breaks instead of
  // diving to the floor and back.
  const dtPaths: Record<string, string> = {}
  dtKeys.forEach((dt) => {
    let path = ''
    let open = false
    dataPoints.forEach((bp, i) => {
      const loading = bp.transformers?.[dt]?.loading
      if (loading == null || Number.isNaN(loading)) { open = false; return }
      path += `${open ? ' L ' : (path ? ' M ' : 'M ')}${getX(i)},${getY(loading * 100)}`
      open = true
    })
    dtPaths[dt] = path
  })

  // Clearing price. `?? 4.5` was here: a block in which nothing cleared was
  // drawn at a plausible-looking ₹4.50, which on a night-time street is most of
  // the chart. No clearing price is a gap in the line.
  let pricePath = ''
  let priceOpen = false
  dataPoints.forEach((bp, i) => {
    if (bp.clearing_price == null) { priceOpen = false; return }
    pricePath += `${priceOpen ? ' L ' : (pricePath ? ' M ' : 'M ')}${getX(i)},${getPriceY(bp.clearing_price)}`
    priceOpen = true
  })

  // Active block index
  const activeIdx = dataPoints.findIndex((bp) => bp.block === currentBlock?.block)

  const activeOrHovered = hoveredIdx !== null ? dataPoints[hoveredIdx] : currentBlock

  /** Most-loaded transformer in the block on screen, whichever one that is. */
  const worstDt = useMemo(() => {
    const entries = Object.entries(activeOrHovered?.transformers ?? {})
      .filter(([, state]) => state?.loading != null)
    if (entries.length === 0) return null
    const [id, state] = entries.reduce((a, b) => (b[1].loading > a[1].loading ? b : a))
    return { id, loading: state.loading }
  }, [activeOrHovered])

  return (
    <div className="telemetry-modal-backdrop" onClick={onClose}>
      <div
        className="telemetry-drawer"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Real-time grid telemetry and load dynamics"
      >
        <div className="telemetry-header">
          <div className="telemetry-title-group">
            <span className="telemetry-pulse-dot" />
            <h3>REAL-TIME GRID TELEMETRY &amp; LOAD DYNAMICS</h3>
            <span className="telemetry-tag">IEEE C57.91 &amp; P2P Market</span>
          </div>
          <div className="telemetry-controls-group">
            <div className="telemetry-legend">
              {dtKeys.map((dt, i) => (
                <span key={dt} className="legend-item" style={{ color: colorFor(dt, i) }}>
                  <span className="legend-color-box" style={{ background: colorFor(dt, i) }} />
                  {dt}
                </span>
              ))}
              <span className="legend-item" style={{ color: '#eab308' }}>
                <span className="legend-color-box" style={{ background: '#eab308' }} />
                Price (₹)
              </span>
              <span className="legend-item limit-legend">
                <span className="legend-dashed-line" />
                100% Limit
              </span>
            </div>
            <button className="telemetry-close-btn" onClick={onClose} title="Close Graph (G or Esc)" aria-label="Close">
              ✕
            </button>
          </div>
        </div>

      {/* Snapshot Cards */}
      <div className="telemetry-stats-row">
        <div className="telemetry-stat-card">
          <span className="stat-name">Active Time</span>
          <strong className="stat-highlight">{activeOrHovered?.clock ?? '—'}</strong>
          <small>Block #{activeOrHovered?.block ?? 0}</small>
        </div>
        <div className="telemetry-stat-card">
          {/* Was pinned to DT-3. Which transformer is worst changes hour to
              hour, and it is the worst one that decides whether the street
              breaches. */}
          <span className="stat-name">Peak DT Load{worstDt ? ` · ${worstDt.id}` : ''}</span>
          <strong className={`stat-highlight ${worstDt && worstDt.loading > 1.0 ? 'danger-text' : ''}`}>
            {worstDt ? `${Math.round(worstDt.loading * 100)}%` : '—'}
          </strong>
          <small>
            {!worstDt ? 'No reading' : worstDt.loading > 1.0 ? '⚠️ Overload Breach' : 'Nominal'}
          </small>
        </div>
        <div className="telemetry-stat-card">
          <span className="stat-name">P2P Clearing Price</span>
          <strong className="stat-highlight">
            {activeOrHovered?.clearing_price != null
              ? `₹${activeOrHovered.clearing_price.toFixed(2)}`
              : '—'}
          </strong>
          <small>per kWh</small>
        </div>
        <div className="telemetry-stat-card">
          <span className="stat-name">Battery Reshape Discharge</span>
          <strong className="stat-highlight">
            {activeOrHovered?.battery?.discharged_kwh != null
              ? `${activeOrHovered.battery.discharged_kwh.toFixed(1)} kWh`
              : '—'}
          </strong>
          <small>Autonomous LP Flow</small>
        </div>
      </div>

      {/* Interactive SVG Chart */}
      <div className="telemetry-svg-container">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="telemetry-svg"
          // 'none' was here, which stretched stroke widths and axis labels
          // non-uniformly at every viewport that is not exactly 1440x380.
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="Transformer loading and P2P clearing price over the last blocks"
        >
          {/* Danger zone >100% */}
          <rect
            x={padding.left}
            y={padding.top}
            width={plotWidth}
            height={y100 - padding.top}
            fill="rgba(239, 68, 68, 0.08)"
          />

          {/* Grid lines */}
          {Array.from({ length: Math.floor(maxY / 25) + 1 }, (_, i) => i * 25)
            .concat(100)
            .filter((level, i, all) => all.indexOf(level) === i && level <= maxY)
            .sort((a, b) => a - b)
            .map((level) => {
            const y = getY(level)
            return (
              <g key={level}>
                <line
                  x1={padding.left}
                  y1={y}
                  x2={width - padding.right}
                  y2={y}
                  stroke={level === 100 ? 'rgba(239, 68, 68, 0.65)' : 'rgba(255, 255, 255, 0.08)'}
                  strokeDasharray={level === 100 ? '5 4' : undefined}
                  strokeWidth={level === 100 ? 1.8 : 1}
                />
                <text
                  x={padding.left - 10}
                  y={y + 4}
                  textAnchor="end"
                  fill={level === 100 ? '#ef4444' : 'rgba(255, 255, 255, 0.45)'}
                  fontSize="11"
                  fontFamily="monospace"
                >
                  {level}%
                </text>
              </g>
            )
          })}

          {/* Right-hand price axis. The clearing price was plotted against its
              own hidden 0–₹10 scale with no axis anywhere, so the dashed line
              could be seen rising and falling but no value could be read off
              it — and at a glance it sat among the loading curves as if it
              were another percentage. */}
          {[0, 2.5, 5, 7.5, 10].map((price) => (
            <text
              key={`price-${price}`}
              x={width - padding.right + 10}
              y={getPriceY(price) + 4}
              textAnchor="start"
              fill="rgba(234, 179, 8, 0.65)"
              fontSize="11"
              fontFamily="monospace"
            >
              ₹{price}
            </text>
          ))}
          <text
            x={width - padding.right + 10}
            y={padding.top - 14}
            textAnchor="start"
            fill="#eab308"
            fontSize="10.5"
            fontFamily="monospace"
            letterSpacing="0.6"
          >
            ₹/kWh
          </text>
          <text
            x={padding.left - 10}
            y={padding.top - 14}
            textAnchor="end"
            fill="rgba(255, 255, 255, 0.55)"
            fontSize="10.5"
            fontFamily="monospace"
            letterSpacing="0.6"
          >
            DT LOAD
          </text>

          {/* Time axis marks */}
          {dataPoints.map((bp, i) => {
            if (dataPoints.length > 12 && i % 3 !== 0 && i !== dataPoints.length - 1) return null
            const x = getX(i)
            return (
              <g key={bp.block}>
                <line
                  x1={x}
                  y1={padding.top + plotHeight}
                  x2={x}
                  y2={padding.top + plotHeight + 6}
                  stroke="rgba(255, 255, 255, 0.25)"
                />
                <text
                  x={x}
                  y={padding.top + plotHeight + 20}
                  textAnchor="middle"
                  fill="rgba(255, 255, 255, 0.5)"
                  fontSize="10.5"
                  fontFamily="monospace"
                >
                  {bp.clock}
                </text>
              </g>
            )
          })}

          {/* 100% capacity label with pill badge */}
          <g>
            <rect
              x={width - padding.right - 175}
              y={y100 - 22}
              width={170}
              height={22}
              rx={6}
              fill="rgba(239, 68, 68, 0.22)"
              stroke="rgba(239, 68, 68, 0.65)"
              strokeWidth={1.2}
            />
            <text
              x={width - padding.right - 90}
              y={y100 - 7}
              textAnchor="middle"
              fill="#fca5a5"
              fontSize="11"
              fontWeight="bold"
              letterSpacing="0.8"
              fontFamily="monospace"
            >
              CRITICAL LIMIT 100%
            </text>
          </g>

          {/* DT Loading Paths */}
          {dtKeys.map((dt, i) => (
            <path
              key={dt}
              d={dtPaths[dt]}
              fill="none"
              stroke={colorFor(dt, i)}
              // Emphasis follows the transformer under the cursor, not a
              // hardcoded DT-3.
              strokeWidth={dt === worstDt?.id ? '3.2' : '2.0'}
              strokeOpacity={dt === worstDt?.id ? '1' : '0.85'}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}

          {/* Clearing price path */}
          <path
            d={pricePath}
            fill="none"
            stroke="#eab308"
            strokeWidth="2.4"
            strokeDasharray="4 3"
            strokeOpacity="0.9"
          />

          {/* Breach indicators */}
          {dataPoints.map((bp, i) => {
            // One dot per breaching transformer, on that transformer's own
            // line. This used to mark every breach at DT-3's height, so a DT-1
            // overload put a red dot on a healthy DT-3 reading.
            const breaching = dtKeys.filter((dt) => (bp.transformers?.[dt]?.loading ?? 0) > 1.0)
            if (breaching.length === 0) return null
            const x = getX(i)
            return (
              <g key={`breach-${bp.block}`}>
                {breaching.map((dt) => (
                  <circle
                    key={dt}
                    cx={x}
                    cy={getY((bp.transformers?.[dt]?.loading ?? 0) * 100)}
                    r="4"
                    fill="#ef4444"
                    stroke="#fff"
                    strokeWidth="1"
                  />
                ))}
              </g>
            )
          })}

          {/* Active / scrubber block vertical line */}
          {activeIdx >= 0 && (
            <g>
              <line
                x1={getX(activeIdx)}
                y1={padding.top}
                x2={getX(activeIdx)}
                y2={padding.top + plotHeight}
                stroke="#06b6d4"
                strokeWidth="2"
              />
              <circle
                cx={getX(activeIdx)}
                cy={padding.top + 3}
                r="3"
                fill="#06b6d4"
              />
            </g>
          )}

          {/* Interactive hover overlay columns */}
          {dataPoints.map((bp, i) => {
            const x = getX(i)
            const colWidth =
              dataPoints.length > 1 ? plotWidth / (dataPoints.length - 1) : plotWidth
            return (
              <rect
                key={`hover-${bp.block}`}
                x={x - colWidth / 2}
                y={padding.top}
                width={colWidth}
                height={plotHeight}
                fill="transparent"
                style={{ cursor: onSeek ? 'pointer' : 'default' }}
                onMouseEnter={() => setHoveredIdx(i)}
                onMouseLeave={() => setHoveredIdx(null)}
                onClick={() => onSeek?.(bp.block)}
              />
            )
          })}
        </svg>
      </div>

      <div className="telemetry-footer">
        <span className="footer-hint">
          💡 Click anywhere on the timeline to seek to that block · Solid lines: Transformer Load · Dashed: Clearing Price (₹/kWh)
        </span>
        <span className="footer-status">
          Sentinel: <strong className="green-text">Autonomous Active</strong> · Flow Agent: <strong className="green-text">LP Armed</strong>
        </span>
      </div>
      </div>
    </div>
  )
}
