export type TransportStatus = 'live' | 'replay' | 'stale' | 'disconnected'
export type BlockStatus = 'cleared' | 'reshaped' | 'fallback'
export type HouseState = 'export' | 'import' | 'idle'
export type Phase = 'A' | 'B' | 'C'

export interface SceneHouse {
  id: string
  transformer: string
  phase: Phase
  x: number
  y: number
  has_pv: boolean
  has_battery: boolean
  kind?: 'premise' | 'evhub'
}

export interface SceneTransformer {
  id: string
  rating_kva: number
  x: number
  y: number
}

export interface ScenePayload {
  houses: SceneHouse[]
  transformers: SceneTransformer[]
  blocks_per_day: number
  replay_rate: number
}

export interface HouseBlockState {
  net_kwh: number
  state: HouseState
  soc_frac: number | null
  curtailed: number
}

export interface TransformerBlockState {
  loading: number
  hotspot_c: number
  life_used_frac: number
  stressed: boolean
}

export interface TradePayload {
  from: string
  to: string
  kwh: number
  price: number
  curtailed: number
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
  transformerLifeBaseline: number
  transformerLifeUrjasetu: number
  deferredCapex: number
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
  command(name: string, args: Record<string, unknown>): void
  seek(block: number): void
  start(): void
  stop(): void
}
