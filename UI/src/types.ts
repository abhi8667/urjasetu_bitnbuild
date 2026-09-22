// The transport contract. Every field here is produced by `server/payloads.py`
// from engine data — nothing in this file is a shape the UI invented.
//
// Fields marked NEW did not exist when the UI ran on `demoFixture.ts`. The UI
// was guessing them instead: PV capacity was the string "4.8 kWp" typed into
// App.tsx, battery capacity was "10 kWh LiFePO4", and state of charge fell back
// to 0.78 whenever the payload had none. Those are real per-premises values in
// the registry and they now travel with the scene.

export type TransportStatus = 'live' | 'replay' | 'complete' | 'stale' | 'disconnected' | 'connecting'
export type BlockStatus = 'cleared' | 'reshaped' | 'fallback'
export type HouseState = 'export' | 'import' | 'idle'
export type Phase = 'A' | 'B' | 'C'
export type BuildingType = 'res' | 'apt' | 'com' | 'evhub'
export type BreachKind = 'loading' | 'phase' | 'voltage'

export interface SceneHouse {
  id: string
  transformer: string
  phase: Phase
  /** Metres east of the street's centroid, projected from the surveyed lat/lon. */
  x: number
  /** Metres north of the street's centroid. */
  y: number
  has_pv: boolean
  has_battery: boolean
  kind?: 'premise' | 'evhub'
  // NEW — real registry values, per premises.
  building_type?: BuildingType
  pv_kw?: number
  battery_kwh?: number
  battery_max_kw?: number
  retail_tariff?: number
  distance_m?: number
  transmission_loss_pct?: number
}

export interface SceneTransformer {
  id: string
  rating_kva: number
  x: number
  y: number
  // NEW
  name?: string
  feeder_id?: string
  /** As installed per transformer_registry.json, before the engine's override. */
  registry_kva?: number | null
}

export interface ScenePayload {
  houses: SceneHouse[]
  transformers: SceneTransformer[]
  blocks_per_day: number
  replay_rate: number
  // NEW
  origin?: { lat: number; lon: number }
  power_factor?: number
  loading_limit?: number
  /** Number of hourly blocks in this finite simulation run. */
  total_blocks?: number
  /** Engine-selected checkpoints; avoids hardcoding a clock with no event. */
  scenario_blocks?: { battery_dispatch?: number | null }
}

export interface HouseBlockState {
  net_kwh: number
  state: HouseState
  /** null when the premises has no battery. Never a placeholder. */
  soc_frac: number | null
  curtailed: number
  /** Energy that physically entered or left this premises' battery in this block. */
  battery_charged_kwh?: number
  battery_discharged_kwh?: number
}

export interface TransformerBlockState {
  loading: number
  /** null before the health agent has produced a state for this block. */
  hotspot_c: number | null
  life_used_frac: number | null
  stressed: boolean
  // NEW
  /** Cumulative loss of life in equivalent HOURS. `life_used_frac` is a
   *  fraction of a 180,000-hour rating and is ~1e-5 over a month, which renders
   *  as "0.0000%" — hours is the legible unit and the one the thermal model
   *  actually works in. */
  life_used_hours?: number | null
  ageing_adder_inr?: number
  predicted_breach?: boolean
}

export interface TradePayload {
  from: string
  to: string
  kwh: number
  price: number
  curtailed: number
  /** NEW — what was struck before the flow agent curtailed it. */
  requested_kwh?: number
}

export interface BatteryDispatchPayload {
  from: string
  to: string
  kwh: number
  transformer_id: string
  kind: 'feeder_support'
}

export interface BlockPayload {
  block: number
  clock: string
  day: number
  clearing_price: number | null
  status: BlockStatus
  houses: Record<string, HouseBlockState>
  transformers: Record<string, TransformerBlockState>
  trades: TradePayload[]
  // NEW
  breach?: { transformer_id: string; kind: BreachKind; severity: number } | null
  battery?: {
    charged_kwh: number
    discharged_kwh: number
    dispatches?: BatteryDispatchPayload[]
  }
  settlement?: { bill_lines: number; charges_inr: number }
}

export interface EventPayload {
  block: number
  agent: string
  kind: string
  text: string
}

export interface RunSummary {
  days: number
  houses: number
  transformers: number
  householdBillBaseline: number
  householdBillUrjasetu: number
  discomRevenueBaseline: number
  discomRevenueUrjasetu: number
  /** Cumulative loss-of-life HOURS, not a percentage. */
  transformerLifeBaseline: number
  transformerLifeUrjasetu: number
  deferredCapex: number
  // NEW — all computed by the engine, none typed in.
  deferredCapexAnnualised?: number
  /** The check the whole argument rests on. Shown, not assumed. */
  baselineAgesAtLeastAsFast?: boolean
  lifeSavedHours?: number
  householdSavingInr?: number
  discomGainInr?: number
}

export interface DemoRun {
  scene: ScenePayload
  blocks: BlockPayload[]
  events: EventPayload[]
  summary: RunSummary
}

export interface Transport {
  onScene(cb: (scene: ScenePayload) => void): () => void
  onBlock(cb: (block: BlockPayload) => void): () => void
  onEvent(cb: (event: EventPayload) => void): () => void
  onStatus(cb: (status: TransportStatus) => void): () => void
  /** NEW — the engine streams its own run summary; the fixture could not. */
  onSummary?(cb: (summary: RunSummary) => void): () => void
  command(name: string, args: Record<string, unknown>): void
  seek(block: number): void
  start(): void
  stop(): void
}
