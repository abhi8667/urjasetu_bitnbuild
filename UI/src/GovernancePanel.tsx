/**
 * GovernancePanel — Governance & Compliance + Operations Briefing
 *
 * Two agents, one panel, two tabs:
 *
 *   AUDIT     Rule-based incident trail (GC-01 … GC-12), per-day summaries,
 *             cross-day fairness table. Fully deterministic and verifiable.
 *
 *   BRIEFINGS Plain-language daily operations briefings for non-technical
 *             administrators. Template-based; LLM-enriched when the server
 *             has a Groq key.
 *
 * Fetches from /api/governance and /api/briefings on mount.
 * Falls back gracefully when the engine is unreachable.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { ENGINE_HTTP } from './config'

// ------------------------------------------------------------------ types

interface Incident {
  rule: string
  severity: 'critical' | 'warning' | 'info'
  block: number
  day: number
  clock: string
  subject: string
  message: string
  detail: Record<string, unknown>
}

interface DayAudit {
  day: number
  headline: string
  incidents: number
  critical: number
  warning: number
  info: number
  transformer_breach_blocks: Record<string, number>
  transformer_peak_loading: Record<string, number>
  transformer_peak_hotspot: Record<string, number>
  settlement_reconciled: boolean
  total_charges_inr: number
  blocks_with_trades: number
  price_breach_blocks: number
  max_clearing_price: number
  total_curtailment_events: number
  concentrated_curtailment_houses: string[]
  houses_with_zero_p2p: string[]
}

interface GovernanceData {
  summary: { total_incidents: number; critical: number; warning: number; info: number }
  fairness: {
    house_p2p_received_kwh: Record<string, number>
    house_curtailed_blocks: Record<string, number>
    house_above_avg_charge_days: Record<string, number>
  }
  days: DayAudit[]
  incidents: Incident[]
}

interface Briefing {
  day: number
  mode: 'llm' | 'template' | 'unavailable'
  what_changed: string
  needs_attention: string
  recommendation: string
  severity_summary: { critical: number; warning: number; info: number }
  linked_incidents: Incident[]
}

// ------------------------------------------------------------------ helpers

const SEV_COLOR: Record<string, string> = {
  critical: '#ef4444',
  warning: '#f59e0b',
  info: '#00f0ff',
}

const SEV_BG: Record<string, string> = {
  critical: 'rgba(239,68,68,0.12)',
  warning: 'rgba(245,158,11,0.12)',
  info: 'rgba(0,240,255,0.08)',
}

function SevBadge({ sev }: { sev: string }) {
  return (
    <span style={{
      display: 'inline-block',
      padding: '1px 7px',
      borderRadius: 4,
      fontSize: 10,
      fontWeight: 700,
      letterSpacing: '0.06em',
      textTransform: 'uppercase',
      color: SEV_COLOR[sev] ?? '#94a3b8',
      background: SEV_BG[sev] ?? 'rgba(255,255,255,0.06)',
      border: `1px solid ${SEV_COLOR[sev] ?? '#94a3b8'}44`,
    }}>
      {sev}
    </span>
  )
}

function RuleBadge({ rule }: { rule: string }) {
  return (
    <span style={{
      display: 'inline-block',
      padding: '1px 6px',
      borderRadius: 3,
      fontSize: 10,
      fontWeight: 700,
      fontFamily: 'monospace',
      color: '#bc8aff',
      background: 'rgba(188,138,255,0.1)',
      border: '1px solid rgba(188,138,255,0.25)',
    }}>
      {rule}
    </span>
  )
}

function LoadBar({ value, warn = 85, crit = 100 }: { value: number; warn?: number; crit?: number }) {
  const pct = Math.min(Math.max(value, 0), 160)
  const color = pct >= crit ? '#ef4444' : pct >= warn ? '#f59e0b' : '#10b981'
  return (
    <div style={{ position: 'relative', height: 6, borderRadius: 3, background: 'rgba(255,255,255,0.08)', minWidth: 80, flex: 1 }}>
      <div style={{
        position: 'absolute', left: 0, top: 0, height: '100%',
        width: `${Math.min(pct, 100)}%`,
        borderRadius: 3,
        background: color,
        transition: 'width 0.3s ease',
      }} />
      {pct > 100 && (
        <div style={{
          position: 'absolute', right: 0, top: 0, height: '100%',
          width: `${Math.min((pct - 100) / 60 * 100, 100) * 0.3}%`,
          background: '#ef4444aa',
          borderRadius: 3,
        }} />
      )}
    </div>
  )
}

// ------------------------------------------------------------------ sub-tabs

function IncidentList({ incidents }: { incidents: Incident[] }) {
  const [filter, setFilter] = useState<'all' | 'critical' | 'warning' | 'info'>('all')
  const shown = filter === 'all' ? incidents : incidents.filter((i) => i.severity === filter)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Filter pills */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {(['all', 'critical', 'warning', 'info'] as const).map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            style={{
              padding: '3px 10px',
              borderRadius: 12,
              fontSize: 11,
              fontWeight: filter === s ? 700 : 400,
              background: filter === s
                ? (s === 'all' ? 'rgba(255,255,255,0.15)' : SEV_BG[s])
                : 'rgba(255,255,255,0.05)',
              color: s === 'all' ? '#e2e8f0' : SEV_COLOR[s],
              border: `1px solid ${s === 'all' ? 'rgba(255,255,255,0.15)' : (SEV_COLOR[s] ?? '#94a3b8') + '44'}`,
              cursor: 'pointer',
            }}
          >
            {s === 'all' ? `All (${incidents.length})` : `${s} (${incidents.filter((i) => i.severity === s).length})`}
          </button>
        ))}
      </div>

      {shown.length === 0 && (
        <p style={{ color: '#64748b', fontSize: 12, padding: '12px 0' }}>
          No incidents for this filter.
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 420, overflowY: 'auto' }}>
        {shown.map((inc, idx) => (
          <div key={`${inc.block}-${inc.rule}-${idx}`} style={{
            background: SEV_BG[inc.severity] ?? 'rgba(255,255,255,0.04)',
            border: `1px solid ${(SEV_COLOR[inc.severity] ?? '#94a3b8') + '33'}`,
            borderRadius: 6,
            padding: '8px 10px',
            fontSize: 12,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4, flexWrap: 'wrap' }}>
              <RuleBadge rule={inc.rule} />
              <SevBadge sev={inc.severity} />
              <span style={{ color: '#64748b', fontSize: 10 }}>
                Block {inc.block} · {inc.clock} · Day {inc.day + 1}
              </span>
              <span style={{
                marginLeft: 'auto',
                color: '#94a3b8',
                fontSize: 10,
                fontFamily: 'monospace',
                background: 'rgba(255,255,255,0.06)',
                padding: '1px 5px',
                borderRadius: 3,
              }}>
                {inc.subject}
              </span>
            </div>
            <p style={{ color: '#e2e8f0', lineHeight: 1.5 }}>{inc.message}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function DaySummaryTable({ days }: { days: DayAudit[] }) {
  const [expanded, setExpanded] = useState<number | null>(null)
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 7
  const totalPages = Math.ceil(days.length / PAGE_SIZE)
  const pageSlice = days.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {/* Pagination controls */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11, color: '#94a3b8' }}>
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            style={{ padding: '2px 8px', borderRadius: 4, background: 'rgba(255,255,255,0.08)', color: '#e2e8f0', border: '1px solid rgba(255,255,255,0.1)' }}
          >◀</button>
          <span>Days {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, days.length)} of {days.length}</span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page === totalPages - 1}
            style={{ padding: '2px 8px', borderRadius: 4, background: 'rgba(255,255,255,0.08)', color: '#e2e8f0', border: '1px solid rgba(255,255,255,0.1)' }}
          >▶</button>
        </div>
      )}

      {pageSlice.map((d) => {
        const isOpen = expanded === d.day
        const hasCrits = d.critical > 0
        const hasWarns = d.warning > 0
        return (
          <div key={d.day} style={{
            background: 'rgba(255,255,255,0.04)',
            border: `1px solid ${hasCrits ? 'rgba(239,68,68,0.3)' : hasWarns ? 'rgba(245,158,11,0.25)' : 'rgba(255,255,255,0.08)'}`,
            borderRadius: 6,
            overflow: 'hidden',
          }}>
            {/* Header row */}
            <button
              onClick={() => setExpanded(isOpen ? null : d.day)}
              style={{
                width: '100%', display: 'flex', alignItems: 'center',
                gap: 8, padding: '8px 10px', background: 'transparent',
                cursor: 'pointer', textAlign: 'left',
              }}
            >
              <span style={{ fontSize: 11, color: '#64748b', minWidth: 46 }}>Day {d.day + 1}</span>
              <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                {d.critical > 0 && <SevBadge sev="critical" />}
                {d.warning > 0 && <SevBadge sev="warning" />}
                {d.info > 0 && <SevBadge sev="info" />}
                {d.incidents === 0 && <span style={{ fontSize: 10, color: '#10b981' }}>✓ clean</span>}
              </div>
              <span style={{ flex: 1, fontSize: 11, color: '#94a3b8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {d.headline}
              </span>
              <span style={{ color: '#64748b', fontSize: 12 }}>{isOpen ? '▲' : '▼'}</span>
            </button>

            {isOpen && (
              <div style={{ padding: '6px 10px 10px', borderTop: '1px solid rgba(255,255,255,0.06)', fontSize: 11 }}>
                {/* Transformer loading bars */}
                {Object.keys(d.transformer_peak_loading).length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 8 }}>
                    <span style={{ color: '#64748b', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Peak transformer loading</span>
                    {Object.entries(d.transformer_peak_loading).map(([tid, frac]) => {
                      const pct = frac * 100
                      return (
                        <div key={tid} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{ minWidth: 40, color: '#e2e8f0', fontFamily: 'monospace' }}>{tid}</span>
                          <LoadBar value={pct} />
                          <span style={{
                            minWidth: 42, textAlign: 'right',
                            color: pct >= 100 ? '#ef4444' : pct >= 85 ? '#f59e0b' : '#10b981',
                            fontFamily: 'monospace',
                          }}>{pct.toFixed(1)}%</span>
                          {d.transformer_peak_hotspot[tid] != null && (
                            <span style={{ color: '#64748b', minWidth: 48 }}>
                              {d.transformer_peak_hotspot[tid].toFixed(1)} °C
                            </span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}

                {/* Settlement & market */}
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
                  <span style={{ color: d.settlement_reconciled ? '#10b981' : '#ef4444' }}>
                    {d.settlement_reconciled ? '✓ Settled' : '✗ Settlement mismatch'}
                  </span>
                  <span style={{ color: '#94a3b8' }}>
                    {d.blocks_with_trades} trading block{d.blocks_with_trades !== 1 ? 's' : ''}
                  </span>
                  {d.max_clearing_price > 0 && (
                    <span style={{ color: d.max_clearing_price > 7.0 ? '#f59e0b' : '#94a3b8' }}>
                      Peak ₹{d.max_clearing_price.toFixed(2)}/kWh
                    </span>
                  )}
                  <span style={{ color: '#94a3b8' }}>₹{d.total_charges_inr.toFixed(2)} collected</span>
                </div>

                {/* Fairness flags */}
                {d.concentrated_curtailment_houses.length > 0 && (
                  <p style={{ color: '#f59e0b', fontSize: 11 }}>
                    ⚠ Curtailment concentrated: {d.concentrated_curtailment_houses.join(', ')}
                  </p>
                )}
                {d.houses_with_zero_p2p.length > 0 && (
                  <p style={{ color: '#64748b', fontSize: 11 }}>
                    {d.houses_with_zero_p2p.length} importing house(s) received no P2P energy today.
                  </p>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function FairnessTable({ fairness }: { fairness: GovernanceData['fairness'] }) {
  type FRow = { id: string; p2p_kwh: number; curtailed_blocks: number; above_avg_days: number }
  const rows: FRow[] = useMemo(() => {
    const ids = new Set([
      ...Object.keys(fairness.house_p2p_received_kwh),
      ...Object.keys(fairness.house_curtailed_blocks),
      ...Object.keys(fairness.house_above_avg_charge_days),
    ])
    return Array.from(ids).map((id) => ({
      id,
      p2p_kwh: fairness.house_p2p_received_kwh[id] ?? 0,
      curtailed_blocks: fairness.house_curtailed_blocks[id] ?? 0,
      above_avg_days: fairness.house_above_avg_charge_days[id] ?? 0,
    })).sort((a, b) => b.curtailed_blocks - a.curtailed_blocks || a.p2p_kwh - b.p2p_kwh)
  }, [fairness])

  const [sort, setSort] = useState<'curtailed' | 'p2p' | 'id'>('curtailed')
  const sorted = useMemo(() => {
    const copy = [...rows]
    if (sort === 'curtailed') copy.sort((a, b) => b.curtailed_blocks - a.curtailed_blocks)
    else if (sort === 'p2p') copy.sort((a, b) => a.p2p_kwh - b.p2p_kwh)
    else copy.sort((a, b) => a.id.localeCompare(b.id))
    return copy
  }, [rows, sort])

  const maxP2p = Math.max(...rows.map((r) => r.p2p_kwh), 1)
  const maxCurtailed = Math.max(...rows.map((r) => r.curtailed_blocks), 1)

  const th: React.CSSProperties = {
    padding: '4px 6px', fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
    letterSpacing: '0.06em', color: '#64748b', textAlign: 'left', cursor: 'pointer',
  }

  return (
    <div style={{ overflowY: 'auto', maxHeight: 400 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            <th style={th} onClick={() => setSort('id')}>House {sort === 'id' && '▲'}</th>
            <th style={th} onClick={() => setSort('p2p')}>P2P received (kWh) {sort === 'p2p' && '▲'}</th>
            <th style={th} onClick={() => setSort('curtailed')}>Curtailed blocks {sort === 'curtailed' && '▼'}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
              <td style={{ padding: '4px 6px', fontFamily: 'monospace', color: '#e2e8f0' }}>{row.id}</td>
              <td style={{ padding: '4px 6px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <div style={{
                    height: 6, borderRadius: 3,
                    width: `${Math.max(4, (row.p2p_kwh / maxP2p) * 80)}px`,
                    background: row.p2p_kwh === 0 ? '#64748b' : '#10b981',
                  }} />
                  <span style={{ color: row.p2p_kwh === 0 ? '#64748b' : '#e2e8f0' }}>
                    {row.p2p_kwh === 0 ? '—' : row.p2p_kwh.toFixed(1)}
                  </span>
                </div>
              </td>
              <td style={{ padding: '4px 6px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  {row.curtailed_blocks > 0 && (
                    <div style={{
                      height: 6, borderRadius: 3,
                      width: `${Math.max(4, (row.curtailed_blocks / maxCurtailed) * 80)}px`,
                      background: '#f59e0b',
                    }} />
                  )}
                  <span style={{ color: row.curtailed_blocks > 0 ? '#f59e0b' : '#64748b' }}>
                    {row.curtailed_blocks === 0 ? '—' : row.curtailed_blocks}
                  </span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && (
        <p style={{ color: '#64748b', fontSize: 12, padding: '12px 0' }}>
          No fairness data available yet.
        </p>
      )}
    </div>
  )
}

function BriefingList({ briefings }: { briefings: Briefing[] }) {
  const [selected, setSelected] = useState<number>(briefings.length > 0 ? briefings[briefings.length - 1].day : 0)
  const current = briefings.find((b) => b.day === selected) ?? briefings[briefings.length - 1]
  const scrollRef = useRef<HTMLDivElement>(null)

  if (briefings.length === 0) {
    return <p style={{ color: '#64748b', fontSize: 12, padding: '12px 0' }}>No briefings available.</p>
  }

  const isLLM = current?.mode === 'llm'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* Day selector */}
      <div
        ref={scrollRef}
        style={{ display: 'flex', gap: 5, overflowX: 'auto', paddingBottom: 4 }}
      >
        {briefings.map((b) => {
          const hasCrit = b.severity_summary.critical > 0
          const hasWarn = b.severity_summary.warning > 0
          return (
            <button
              key={b.day}
              onClick={() => setSelected(b.day)}
              style={{
                flexShrink: 0,
                padding: '3px 9px',
                borderRadius: 5,
                fontSize: 11,
                fontWeight: selected === b.day ? 700 : 400,
                background: selected === b.day
                  ? 'rgba(0,240,255,0.15)'
                  : hasCrit ? 'rgba(239,68,68,0.08)' : hasWarn ? 'rgba(245,158,11,0.08)' : 'rgba(255,255,255,0.05)',
                color: selected === b.day ? '#00f0ff' : hasCrit ? '#ef4444' : hasWarn ? '#f59e0b' : '#94a3b8',
                border: `1px solid ${selected === b.day ? 'rgba(0,240,255,0.4)' : hasCrit ? 'rgba(239,68,68,0.25)' : hasWarn ? 'rgba(245,158,11,0.2)' : 'rgba(255,255,255,0.08)'}`,
                cursor: 'pointer',
              }}
            >
              D{b.day + 1}
              {hasCrit && <span style={{ marginLeft: 3, color: '#ef4444' }}>●</span>}
              {!hasCrit && hasWarn && <span style={{ marginLeft: 3, color: '#f59e0b' }}>●</span>}
            </button>
          )
        })}
      </div>

      {current && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
            <span style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0' }}>
              Day {current.day + 1} Briefing
            </span>
            <span style={{
              fontSize: 10, padding: '2px 8px', borderRadius: 10, fontWeight: 700,
              background: isLLM ? 'rgba(188,138,255,0.15)' : 'rgba(0,240,255,0.1)',
              color: isLLM ? '#bc8aff' : '#00f0ff',
              border: `1px solid ${isLLM ? 'rgba(188,138,255,0.35)' : 'rgba(0,240,255,0.25)'}`,
            }}>
              {isLLM ? '✦ LLM-enriched' : '⚙ Deterministic template'}
            </span>
          </div>

          {/* Severity pills */}
          <div style={{ display: 'flex', gap: 6 }}>
            {current.severity_summary.critical > 0 && (
              <SevBadge sev="critical" />
            )}
            {current.severity_summary.warning > 0 && (
              <SevBadge sev="warning" />
            )}
            {current.severity_summary.info > 0 && (
              <SevBadge sev="info" />
            )}
            {current.severity_summary.critical === 0 && current.severity_summary.warning === 0 && (
              <span style={{ fontSize: 11, color: '#10b981' }}>✓ No critical or warning findings</span>
            )}
          </div>

          {/* Three questions */}
          {[
            { label: '📌 What changed today?', text: current.what_changed, color: '#00f0ff' },
            { label: '⚠ What needs attention?', text: current.needs_attention, color: '#f59e0b' },
            { label: '✅ Recommended action', text: current.recommendation, color: '#10b981' },
          ].map(({ label, text, color }) => (
            <div key={label} style={{
              background: 'rgba(255,255,255,0.04)',
              borderRadius: 6,
              padding: '10px 12px',
              borderLeft: `3px solid ${color}`,
            }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color, marginBottom: 5 }}>
                {label}
              </div>
              <p style={{ fontSize: 12, color: '#e2e8f0', lineHeight: 1.65 }}>
                {text || '—'}
              </p>
            </div>
          ))}

          {/* Linked incidents */}
          {current.linked_incidents.length > 0 && (
            <div>
              <p style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: '#64748b', marginBottom: 6 }}>
                Linked audit findings
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {current.linked_incidents.map((inc, i) => (
                  <div key={i} style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    fontSize: 11, color: '#94a3b8',
                    padding: '3px 8px',
                    background: 'rgba(255,255,255,0.03)',
                    borderRadius: 4,
                  }}>
                    <RuleBadge rule={inc.rule} />
                    <SevBadge sev={inc.severity} />
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{inc.message}</span>
                    <span style={{ color: '#475569', fontSize: 10 }}>{inc.clock}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ main component

type PanelTab = 'incidents' | 'days' | 'fairness' | 'briefings'

interface GovernancePanelProps {
  onClose: () => void
}

export function GovernancePanel({ onClose }: GovernancePanelProps) {
  const [tab, setTab] = useState<PanelTab>('incidents')
  const [governance, setGovernance] = useState<GovernanceData | null>(null)
  const [briefings, setBriefings] = useState<Briefing[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    const govUrl = `${ENGINE_HTTP}/api/governance`
    const briefUrl = `${ENGINE_HTTP}/api/briefings`

    Promise.all([
      fetch(govUrl).then((r) => { if (!r.ok) throw new Error(`/api/governance ${r.status}`); return r.json() }),
      fetch(briefUrl).then((r) => { if (!r.ok) throw new Error(`/api/briefings ${r.status}`); return r.json() }),
    ])
      .then(([gov, brief]) => {
        if (cancelled) return
        setGovernance(gov as GovernanceData)
        setBriefings((brief as { briefings: Briefing[] }).briefings ?? [])
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Failed to load governance data')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [])

  const TAB_DEFS: Array<{ id: PanelTab; label: string; count?: number }> = [
    {
      id: 'incidents',
      label: 'Incident Trail',
      count: governance?.summary.total_incidents,
    },
    { id: 'days', label: 'Day Summaries', count: governance?.days.length },
    { id: 'fairness', label: 'Fairness' },
    { id: 'briefings', label: 'Ops Briefings', count: briefings.length },
  ]

  return (
    <div
      className="floating-window window-governance-panel"
      role="dialog"
      aria-label="Governance & Compliance"
      style={{
        position: 'fixed',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        width: 'min(96vw, 760px)',
        maxHeight: '86vh',
        display: 'flex',
        flexDirection: 'column',
        background: 'rgba(10, 16, 28, 0.97)',
        border: '1px solid rgba(255,255,255,0.12)',
        borderRadius: 10,
        boxShadow: '0 24px 80px rgba(0,0,0,0.7)',
        zIndex: 9999,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', padding: '12px 16px',
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(255,255,255,0.03)',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1 }}>
          <svg viewBox="0 0 24 24" fill="none" stroke="#bc8aff" strokeWidth="2" width={16} height={16}>
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            <path d="m9 12 2 2 4-4" />
          </svg>
          <h3 style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
            Governance &amp; Compliance
          </h3>
          {governance && (
            <div style={{ display: 'flex', gap: 5, marginLeft: 8 }}>
              {governance.summary.critical > 0 && (
                <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 10, background: 'rgba(239,68,68,0.15)', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}>
                  {governance.summary.critical} critical
                </span>
              )}
              {governance.summary.warning > 0 && (
                <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 10, background: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.25)' }}>
                  {governance.summary.warning} warnings
                </span>
              )}
              {governance.summary.critical === 0 && governance.summary.warning === 0 && (
                <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 10, background: 'rgba(16,185,129,0.12)', color: '#10b981', border: '1px solid rgba(16,185,129,0.25)' }}>
                  ✓ No critical findings
                </span>
              )}
            </div>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Close Governance Panel"
          style={{
            width: 24, height: 24, borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(255,255,255,0.06)', color: '#94a3b8', fontSize: 14, cursor: 'pointer',
            border: '1px solid rgba(255,255,255,0.1)',
          }}
        >✕</button>
      </div>

      {/* Tab bar */}
      <div style={{
        display: 'flex', gap: 0, borderBottom: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(255,255,255,0.02)',
        flexShrink: 0,
      }}>
        {TAB_DEFS.map(({ id, label, count }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            style={{
              flex: 1, padding: '9px 4px', fontSize: 11, fontWeight: tab === id ? 700 : 400,
              color: tab === id ? '#00f0ff' : '#94a3b8',
              background: 'transparent',
              borderBottom: `2px solid ${tab === id ? '#00f0ff' : 'transparent'}`,
              cursor: 'pointer',
              transition: 'color 0.15s, border-color 0.15s',
            }}
          >
            {label}
            {count != null && count > 0 && (
              <span style={{
                marginLeft: 5, fontSize: 9, padding: '1px 5px', borderRadius: 8,
                background: tab === id ? 'rgba(0,240,255,0.15)' : 'rgba(255,255,255,0.08)',
                color: tab === id ? '#00f0ff' : '#64748b',
              }}>
                {count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '14px 16px' }}>
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, padding: 32, color: '#64748b', fontSize: 13 }}>
            <div style={{
              width: 16, height: 16, borderRadius: '50%',
              border: '2px solid rgba(0,240,255,0.3)',
              borderTopColor: '#00f0ff',
              animation: 'gov-spin 0.8s linear infinite',
            }} />
            Loading audit data…
          </div>
        )}

        {!loading && error && (
          <div style={{
            padding: '14px', borderRadius: 6,
            background: 'rgba(239,68,68,0.1)',
            border: '1px solid rgba(239,68,68,0.25)',
            color: '#fca5a5',
            fontSize: 12,
          }}>
            <strong>Could not load governance data</strong>
            <p style={{ marginTop: 4, color: '#94a3b8' }}>{error}</p>
            <p style={{ marginTop: 4, color: '#64748b' }}>
              This panel requires the Python engine to be running at {ENGINE_HTTP}.
            </p>
          </div>
        )}

        {!loading && !error && governance && (
          <>
            {tab === 'incidents' && (
              <IncidentList incidents={governance.incidents} />
            )}
            {tab === 'days' && (
              <DaySummaryTable days={governance.days} />
            )}
            {tab === 'fairness' && (
              <FairnessTable fairness={governance.fairness} />
            )}
            {tab === 'briefings' && (
              <BriefingList briefings={briefings} />
            )}
          </>
        )}
      </div>

      {/* Footer */}
      <div style={{
        padding: '7px 16px',
        borderTop: '1px solid rgba(255,255,255,0.06)',
        fontSize: 10,
        color: '#475569',
        display: 'flex',
        justifyContent: 'space-between',
        flexShrink: 0,
        background: 'rgba(255,255,255,0.02)',
      }}>
        <span>
          All audit findings are rule-based and deterministic (GC-01–GC-12).
          Ops briefings are {briefings[0]?.mode === 'llm' ? 'LLM-enriched' : 'deterministic template'}.
        </span>
        {governance && (
          <span>
            {governance.summary.total_incidents} finding{governance.summary.total_incidents !== 1 ? 's' : ''} ·{' '}
            {governance.days.length} day{governance.days.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Keyframe for spinner */}
      <style>{`
        @keyframes gov-spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}
