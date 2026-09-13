import React, { useState } from 'react'
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
  const padding = { top: 40, right: 48, bottom: 44, left: 68 }
  const plotWidth = width - padding.left - padding.right
  const plotHeight = height - padding.top - padding.bottom

  // Y-axis range for loading: 0% to 140%
  const maxY = 140
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

  // Generate DT paths
  const dtKeys = ['DT-1', 'DT-2', 'DT-3', 'DT-4']
  const dtPaths: Record<string, string> = {}

  dtKeys.forEach((dt) => {
    const points = dataPoints.map((bp, i) => {
      const loadPct = Math.round((bp.transformers?.[dt]?.loading ?? 0) * 100)
      return `${getX(i)},${getY(loadPct)}`
    })
    dtPaths[dt] = points.length > 0 ? `M ${points.join(' L ')}` : ''
  })

  // Generate Price path
  const pricePoints = dataPoints.map((bp, i) => {
    const price = bp.clearing_price ?? 4.5
    return `${getX(i)},${getPriceY(price)}`
  })
  const pricePath = pricePoints.length > 0 ? `M ${pricePoints.join(' L ')}` : ''

  // Active block index
  const activeIdx = dataPoints.findIndex((bp) => bp.block === currentBlock?.block)

  const activeOrHovered = hoveredIdx !== null ? dataPoints[hoveredIdx] : currentBlock

  return (
    <div className="telemetry-modal-backdrop" onClick={onClose}>
      <div className="telemetry-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="telemetry-header">
          <div className="telemetry-title-group">
            <span className="telemetry-pulse-dot" />
            <h3>REAL-TIME GRID TELEMETRY &amp; LOAD DYNAMICS</h3>
            <span className="telemetry-tag">IEEE C57.91 &amp; P2P Market</span>
          </div>
          <div className="telemetry-controls-group">
            <div className="telemetry-legend">
              {dtKeys.map((dt) => (
                <span key={dt} className="legend-item" style={{ color: DT_COLORS[dt] }}>
                  <span className="legend-color-box" style={{ background: DT_COLORS[dt] }} />
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
          <span className="stat-name">DT-3 Central Load</span>
          <strong
            className={`stat-highlight ${
              (activeOrHovered?.transformers?.['DT-3']?.loading ?? 0) > 1.0 ? 'danger-text' : ''
            }`}
          >
            {Math.round((activeOrHovered?.transformers?.['DT-3']?.loading ?? 0) * 100)}%
          </strong>
          <small>
            {(activeOrHovered?.transformers?.['DT-3']?.loading ?? 0) > 1.0
              ? '⚠️ Overload Breach'
              : 'Nominal'}
          </small>
        </div>
        <div className="telemetry-stat-card">
          <span className="stat-name">P2P Clearing Price</span>
          <strong className="stat-highlight">
            ₹{activeOrHovered?.clearing_price?.toFixed(2) ?? '—'}
          </strong>
          <small>per kWh</small>
        </div>
        <div className="telemetry-stat-card">
          <span className="stat-name">Battery Reshape Discharge</span>
          <strong className="stat-highlight">
            {activeOrHovered?.battery?.discharged_kwh
              ? `${activeOrHovered.battery.discharged_kwh.toFixed(1)} kWh`
              : '0.0 kWh'}
          </strong>
          <small>Autonomous LP Flow</small>
        </div>
      </div>

      {/* Interactive SVG Chart */}
      <div className="telemetry-svg-container">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="telemetry-svg"
          preserveAspectRatio="none"
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
          {[0, 25, 50, 75, 100, 125].map((level) => {
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
          {dtKeys.map((dt) => (
            <path
              key={dt}
              d={dtPaths[dt]}
              fill="none"
              stroke={DT_COLORS[dt]}
              strokeWidth={dt === 'DT-3' ? '3.2' : '2.0'}
              strokeOpacity={dt === 'DT-3' ? '1' : '0.85'}
              strokeLinecap="round"
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
            const hasBreach = dtKeys.some(
              (dt) => (bp.transformers?.[dt]?.loading ?? 0) > 1.0
            )
            if (!hasBreach) return null
            const x = getX(i)
            const dt3Load = Math.round((bp.transformers?.['DT-3']?.loading ?? 0) * 100)
            const y = getY(dt3Load)
            return (
              <g key={`breach-${bp.block}`}>
                <circle cx={x} cy={y} r="4" fill="#ef4444" stroke="#fff" strokeWidth="1" />
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
