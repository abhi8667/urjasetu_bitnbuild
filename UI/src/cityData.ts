export type Point = { x: number; z: number }

export type Building = Point & {
  id: string
  name: string
  floors: number
  footprint: [number, number]
  demandKw: number
  feeder: 'North' | 'Central' | 'South'
}

export type GridNode = Point & {
  id: string
  name: string
  kind: 'generator' | 'main-station' | 'substation' | 'transformer'
  voltageKv: number
  capacityMw: number
  load: number
}

const seeded = (seed: number) => {
  const x = Math.sin(seed * 999.91) * 43758.5453
  return x - Math.floor(x)
}

const roadCenters = [-24, -8, 8, 24]

export const buildings: Building[] = Array.from({ length: 128 }, (_, i) => {
  const col = i % 16
  const row = Math.floor(i / 16)
  let x = -30 + col * 4
  let z = -14 + row * 4
  // Widen the four arterial roads that divide the city into neighbourhoods.
  x += roadCenters.reduce((shift, road) => shift + (x > road ? 1.35 : 0), 0)
  const floors = 1 + Math.floor(seeded(i + 7) * 7)
  const feeder = z < -4 ? 'South' : z > 13 ? 'North' : 'Central'
  return {
    id: `BLD-${String(i + 1).padStart(3, '0')}`,
    name: `${['Asha', 'Kaveri', 'Neem', 'Surya', 'Gulmohar', 'Sahyadri'][i % 6]} ${col + 1}`,
    x: x + (seeded(i + 21) - 0.5) * 0.55,
    z: z + (seeded(i + 81) - 0.5) * 0.55,
    floors,
    footprint: [2.1 + seeded(i + 2) * 1.15, 2.1 + seeded(i + 3) * 1.15],
    demandKw: Math.round((floors * 4.4 + seeded(i + 5) * 17) * 10) / 10,
    feeder,
  }
})

export const gridNodes: GridNode[] = [
  { id: 'GEN-01', name: 'Surya Solar Park', kind: 'generator', x: -37, z: -22, voltageKv: 220, capacityMw: 180, load: 0.72 },
  { id: 'GEN-02', name: 'Nadi Hydro Plant', kind: 'generator', x: 37, z: -20, voltageKv: 220, capacityMw: 240, load: 0.64 },
  { id: 'GS-01', name: 'Urja Nagar Grid', kind: 'main-station', x: 0, z: -25, voltageKv: 220, capacityMw: 360, load: 0.79 },
  { id: 'SS-01', name: 'Uttar Substation', kind: 'substation', x: -22, z: 22, voltageKv: 66, capacityMw: 84, load: 0.68 },
  { id: 'SS-02', name: 'Madhya Substation', kind: 'substation', x: 1, z: 7, voltageKv: 66, capacityMw: 96, load: 0.84 },
  { id: 'SS-03', name: 'Dakshin Substation', kind: 'substation', x: 25, z: -7, voltageKv: 66, capacityMw: 72, load: 0.61 },
  { id: 'TX-01', name: 'Neem Chowk TX', kind: 'transformer', x: -28, z: 12, voltageKv: 11, capacityMw: 8, load: 0.73 },
  { id: 'TX-02', name: 'Station Road TX', kind: 'transformer', x: -10, z: 13, voltageKv: 11, capacityMw: 10, load: 0.89 },
  { id: 'TX-03', name: 'Bazaar TX', kind: 'transformer', x: 13, z: 13, voltageKv: 11, capacityMw: 12, load: 0.82 },
  { id: 'TX-04', name: 'Mill Colony TX', kind: 'transformer', x: 30, z: 12, voltageKv: 11, capacityMw: 7, load: 0.57 },
  { id: 'TX-05', name: 'Lake View TX', kind: 'transformer', x: -20, z: -7, voltageKv: 11, capacityMw: 9, load: 0.77 },
  { id: 'TX-06', name: 'Civil Lines TX', kind: 'transformer', x: 5, z: -8, voltageKv: 11, capacityMw: 11, load: 0.92 },
]

export type PowerLine = {
  id: string
  from: string
  to: string
  voltageKv: number
  utilization: number
}

export const powerLines: PowerLine[] = [
  { id: 'L-001', from: 'GEN-01', to: 'GS-01', voltageKv: 220, utilization: 0.72 },
  { id: 'L-002', from: 'GEN-02', to: 'GS-01', voltageKv: 220, utilization: 0.64 },
  { id: 'L-003', from: 'GS-01', to: 'SS-01', voltageKv: 66, utilization: 0.68 },
  { id: 'L-004', from: 'GS-01', to: 'SS-02', voltageKv: 66, utilization: 0.84 },
  { id: 'L-005', from: 'GS-01', to: 'SS-03', voltageKv: 66, utilization: 0.61 },
  { id: 'L-006', from: 'SS-01', to: 'TX-01', voltageKv: 11, utilization: 0.73 },
  { id: 'L-007', from: 'SS-01', to: 'TX-02', voltageKv: 11, utilization: 0.89 },
  { id: 'L-008', from: 'SS-02', to: 'TX-03', voltageKv: 11, utilization: 0.82 },
  { id: 'L-009', from: 'SS-02', to: 'TX-05', voltageKv: 11, utilization: 0.77 },
  { id: 'L-010', from: 'SS-03', to: 'TX-04', voltageKv: 11, utilization: 0.57 },
  { id: 'L-011', from: 'SS-03', to: 'TX-06', voltageKv: 11, utilization: 0.92 },
]

export const citySummary = {
  city: 'Urja Nagar',
  state: 'Maharashtra',
  totalDemandMw: 287.4,
  renewableShare: 42,
  frequencyHz: 50.02,
  consumers: 18420,
}
