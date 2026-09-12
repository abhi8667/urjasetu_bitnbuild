import type {
  BlockPayload,
  BlockStatus,
  DemoRun,
  EventPayload,
  HouseBlockState,
  Phase,
  SceneHouse,
  ScenePayload,
  TradePayload,
} from './types'

const transformerSpecs = [
  { id: 'DT-1', rating_kva: 125 },
  { id: 'DT-2', rating_kva: 63 },
  { id: 'DT-3', rating_kva: 63 },
  { id: 'DT-4', rating_kva: 63 },
]

const clusterSizes = [12, 17, 14, 17]
const phases: Phase[] = ['A', 'B', 'C']

function mulberry32(seed: number) {
  return () => {
    let value = (seed += 0x6d2b79f5)
    value = Math.imul(value ^ (value >>> 15), value | 1)
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61)
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296
  }
}

function makeScene(): ScenePayload {
  const houses: SceneHouse[] = []
  let numericId = 1
  clusterSizes.forEach((count, transformerIndex) => {
    for (let i = 0; i < count; i += 1) {
      houses.push({
        id: `H-${String(numericId).padStart(2, '0')}`,
        transformer: transformerSpecs[transformerIndex].id,
        phase: phases[i % phases.length],
        x: 0,
        y: 0,
        has_pv: numericId % 3 === 0 || numericId % 11 === 0,
        has_battery: numericId % 8 === 0,
        kind: 'premise',
      })
      numericId += 1
    }
    houses.push({
      id: `EVHUB-DT${transformerIndex + 1}`,
      transformer: transformerSpecs[transformerIndex].id,
      phase: 'A',
      x: 0,
      y: 0,
      has_pv: false,
      has_battery: false,
      kind: 'evhub',
    })
  })
  return {
    houses,
    transformers: transformerSpecs.map((item) => ({ ...item, x: 0, y: 0 })),
    blocks_per_day: 24,
    replay_rate: 60,
  }
}

function clockFor(block: number) {
  return `${String(block % 24).padStart(2, '0')}:00`
}

function makeTrades(
  scene: ScenePayload,
  houseState: Record<string, HouseBlockState>,
  block: number,
  price: number,
  status: BlockStatus,
): TradePayload[] {
  const rng = mulberry32(9000 + block)
  const sellers = scene.houses.filter((house) => houseState[house.id].state === 'export')
  const buyers = scene.houses.filter((house) => houseState[house.id].state === 'import')
  const trades: TradePayload[] = []
  const target = block % 7 === 0 ? 18 : 6 + (block % 8)
  for (let i = 0; i < target && sellers.length && buyers.length; i += 1) {
    const seller = sellers[i % sellers.length]
    const eligible = buyers.filter((buyer) => buyer.transformer === seller.transformer)
    if (!eligible.length) continue
    const buyer = eligible[Math.floor(rng() * eligible.length)]
    trades.push({
      from: seller.id,
      to: buyer.id,
      kwh: Number((0.25 + rng() * 2.4).toFixed(2)),
      price,
      curtailed: status === 'reshaped' && i < 4 ? Number((0.08 + rng() * 0.18).toFixed(2)) : 0,
    })
  }
  return trades
}

function makeBlock(scene: ScenePayload, block: number): BlockPayload {
  const hour = block % 24
  const solar = Math.max(0, Math.sin(((hour - 6) / 12) * Math.PI))
  const evening = Math.exp(-Math.pow((hour - 19) / 2.6, 2))
  const rng = mulberry32(20250912 + block)
  const houses: Record<string, HouseBlockState> = {}

  scene.houses.forEach((house, index) => {
    const baseLoad = house.kind === 'evhub' ? 5.8 + evening * 4.2 : 0.7 + evening * 1.8 + rng() * 0.6
    const generation = house.has_pv ? solar * (2.8 + (index % 4) * 0.7) : 0
    const net = baseLoad - generation
    houses[house.id] = {
      net_kwh: Number(net.toFixed(2)),
      state: Math.abs(net) < 0.18 ? 'idle' : net < 0 ? 'export' : 'import',
      soc_frac: house.has_battery ? Number((0.28 + solar * 0.52 - evening * 0.18 + rng() * 0.08).toFixed(2)) : null,
      curtailed: 0,
    }
  })

  const status: BlockStatus = hour === 19 && block % 48 < 24 ? 'reshaped' : hour === 20 && block % 72 >= 48 ? 'fallback' : 'cleared'
  const transformers = Object.fromEntries(scene.transformers.map((transformer, index) => {
    const loading = Math.max(0.38, 0.48 + evening * (index === 1 ? 0.42 : 0.68 + index * 0.025) + rng() * 0.08)
    return [transformer.id, {
      loading: Number(loading.toFixed(3)),
      hotspot_c: Number((44 + loading * 52).toFixed(1)),
      life_used_frac: Number((block * loading * 0.0000014).toFixed(6)),
      stressed: loading > 1,
    }]
  }))
  const clearingPrice = Number((4.55 + evening * 2.3 - solar * 0.9 + rng() * 0.25).toFixed(2))
  const trades = makeTrades(scene, houses, block, clearingPrice, status)
  trades.forEach((trade) => {
    if (trade.curtailed > 0) houses[trade.from].curtailed = trade.curtailed
  })

  return {
    block,
    clock: clockFor(block),
    day: Math.floor(block / 24) + 1,
    clearing_price: trades.length ? clearingPrice : null,
    status,
    houses,
    transformers,
    trades,
  }
}

function eventsFor(block: BlockPayload): EventPayload[] {
  const volume = block.trades.reduce((sum, trade) => sum + trade.kwh, 0)
  const events: EventPayload[] = [
    { block: block.block, agent: 'prosumer', kind: 'orders_opened', text: `Forecast checked; ${block.trades.length} local matches available` },
    { block: block.block, agent: 'consumer', kind: 'bids_submitted', text: `Spend caps applied for block ${block.block}` },
    { block: block.block, agent: 'market', kind: 'market_cleared', text: `${volume.toFixed(1)} kWh cleared at ₹${(block.clearing_price ?? 0).toFixed(2)}` },
    { block: block.block, agent: 'sentinel', kind: 'constraints_checked', text: `Four transformers checked at ${block.clock}` },
  ]
  if (block.status !== 'cleared') {
    const worst = Object.entries(block.transformers).sort((a, b) => b[1].loading - a[1].loading)[0]
    events.push({ block: block.block, agent: 'sentinel', kind: 'breach_detected', text: `${worst[0]} projected ${(worst[1].loading * 100).toFixed(0)}% at ${block.clock}` })
    events.push({ block: block.block, agent: 'flow', kind: 'trades_reshaped', text: block.status === 'fallback' ? 'Fallback curtailment applied' : 'Batteries absorbed surplus; four trades trimmed' })
    events.push({ block: block.block, agent: 'market', kind: 'market_recleared', text: 'Constrained orders re-cleared within limits' })
  }
  events.push({ block: block.block, agent: 'settlement', kind: 'block_settled', text: 'Charges posted; settlement balanced' })
  return events
}

export function createDemoRun(): DemoRun {
  const scene = makeScene()
  const blocks = Array.from({ length: 72 }, (_, block) => makeBlock(scene, block))
  return {
    scene,
    blocks,
    events: blocks.flatMap(eventsFor),
    summary: {
      days: 30,
      houses: scene.houses.length,
      transformers: scene.transformers.length,
      householdBillBaseline: 1840,
      householdBillUrjasetu: 1612,
      discomRevenueBaseline: 0,
      discomRevenueUrjasetu: 4310,
      transformerLifeBaseline: 0.061,
      transformerLifeUrjasetu: 0.043,
      deferredCapex: 186000,
    },
  }
}

export function applyDemoCommand(run: DemoRun, currentIndex: number, name: string) {
  const target = run.blocks[(currentIndex + 1) % run.blocks.length]
  if (name === 'derate') {
    const state = target.transformers['DT-3']
    state.loading = Math.max(1.18, state.loading)
    state.hotspot_c = 112.4
    state.stressed = true
    target.status = 'reshaped'
    target.trades.slice(0, 4).forEach((trade) => { trade.curtailed = 0.18 })
  }
  if (name === 'cloud') {
    for (let offset = 1; offset <= 8; offset += 1) {
      const block = run.blocks[(currentIndex + offset) % run.blocks.length]
      Object.entries(block.houses).forEach(([id, state]) => {
        const house = run.scene.houses.find((item) => item.id === id)
        if (house?.has_pv && state.state === 'export') {
          state.net_kwh = Number((state.net_kwh * 0.28).toFixed(2))
          if (Math.abs(state.net_kwh) < 0.2) state.state = 'idle'
        }
      })
      if (block.clearing_price !== null) block.clearing_price = Number((block.clearing_price + 0.9).toFixed(2))
    }
  }
}
