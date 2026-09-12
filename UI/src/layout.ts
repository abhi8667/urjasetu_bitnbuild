import type { ScenePayload } from './types'

export interface GridPoint { gx: number; gy: number; gz: number }
export interface ScreenPoint { x: number; y: number }
export interface LayoutResult {
  houses: Record<string, GridPoint>
  transformers: Record<string, GridPoint>
  /** True when the surveyed coordinates were used rather than the fallback grid. */
  geographic: boolean
}

const TILE_SIZE = 30
const COS30 = Math.cos(Math.PI / 6)
const SIN30 = 0.5

/**
 * Grid units the laid-out street should span on its longer axis.
 *
 * The geographic layout MUST be normalised to roughly what the schematic
 * produced, not scaled by a fixed units-per-metre factor. Whitefield's four
 * clusters span about 1.4 km; at any constant scale that fills the camera's
 * frame the buildings are specks, and at any scale that keeps the buildings
 * legible the street runs far outside the camera's framing — which is exactly
 * what happened: the 3D view came back showing roads and streetlights with
 * every building parked off-screen.
 *
 * Normalising to a target extent keeps the geography (relative positions, the
 * cluster shapes, the aspect ratio of the street) and throws away only the
 * absolute scale, which was never meaningful on screen anyway.
 */
const TARGET_EXTENT_UNITS = 22

/** Premises per concentric ring around a transformer. Six keeps neighbouring
 *  buildings about 2.6 units apart at the first ring, which is roughly a
 *  building's own width — close enough to read as a street, far enough not to
 *  intersect. */
const RING_CAPACITY = 6

/** Grid units between rings. */
const RING_SPACING = 2.6

export function project(gx: number, gy: number, gz = 0): ScreenPoint {
  return {
    x: (gx - gy) * COS30 * TILE_SIZE,
    y: (gx + gy) * SIN30 * TILE_SIZE - gz * TILE_SIZE,
  }
}

/**
 * Lay the street out from the engine's coordinates when it sends them.
 *
 * This function used to ignore `scene.houses[].x/y` entirely and build a
 * schematic grid of rows per transformer. That was defensible while the UI ran
 * on a fixture where x and y were literally 0 — but the engine sends the real
 * surveyed Whitefield positions, projected to metres, and using them means the
 * clusters on screen are the clusters on the ground: DT-1's commercial frontage
 * really is separated from DT-4's apartments, and a voltage breach at the far
 * end of a feeder is visibly at the far end.
 *
 * `layoutSchematic` remains as the fallback for a payload with no coordinates
 * (the offline demo fixture), so nothing breaks when the engine is unreachable.
 */
export function layoutScene(scene: ScenePayload): LayoutResult {
  const hasCoordinates = scene.houses.some((house) => house.x !== 0 || house.y !== 0)
  if (!hasCoordinates) return { ...layoutSchematic(scene), geographic: false }

  // TWO SCALES, and this is the whole trick.
  //
  // Whitefield's four transformers sit hundreds of metres apart while the
  // premises on any one of them sit tens of metres apart. Draw both at one
  // scale and you get either four specks or one legible cluster with the other
  // three off-screen — the latter is exactly what a single-scale version of
  // this produced.
  //
  // So: transformer positions are geographic, normalised to the target extent,
  // and premises are placed COMPACTLY around their own DT at the true BEARING
  // from it, with radius ranked by true distance. What survives is everything
  // that carries meaning — which transformers are neighbours, which side of its
  // DT a premises sits on, and which premises are at the far end of a feeder
  // (the ones a voltage breach lands on). What is discarded is only the
  // absolute spacing, which no screen could show usefully anyway.
  const transformers: Record<string, GridPoint> = {}
  const houses: Record<string, GridPoint> = {}

  const txX = scene.transformers.map((t) => t.x)
  const txY = scene.transformers.map((t) => t.y)
  const spanX = Math.max(...txX) - Math.min(...txX)
  const spanY = Math.max(...txY) - Math.min(...txY)
  const span = Math.max(spanX, spanY, 1)
  const clusterGap = Math.max(...scene.transformers.map(
    (t) => scene.houses.filter((h) => h.transformer === t.id).length,
  ))
  // Enough room between DT centres for the biggest cluster's rings plus air.
  const targetSpan = Math.max(TARGET_EXTENT_UNITS, ringsFor(clusterGap) * 5.2)
  const unitsPerMetre = targetSpan / span

  const cx = (Math.max(...txX) + Math.min(...txX)) / 2
  const cy = (Math.max(...txY) + Math.min(...txY)) / 2

  scene.transformers.forEach((transformer) => {
    transformers[transformer.id] = {
      gx: (transformer.x - cx) * unitsPerMetre,
      gy: (transformer.y - cy) * unitsPerMetre,
      gz: 0,
    }
  })

  scene.transformers.forEach((transformer) => {
    const origin = transformers[transformer.id]
    const members = scene.houses
      .filter((house) => house.transformer === transformer.id)
      // By true distance from the DT, so ring order is electrical distance —
      // the near ring is the near end of the feeder and the outer ring is the
      // far end, which is where a voltage breach actually lands.
      .sort((a, b) => (a.distance_m ?? 0) - (b.distance_m ?? 0) || a.id.localeCompare(b.id))

    members.forEach((house, index) => {
      // True bearing from the DT, so a premises stays on the side of its
      // transformer it is really on.
      const bearing = Math.atan2(house.y - transformer.y, house.x - transformer.x)
      const ring = Math.floor(index / RING_CAPACITY) + 1
      const withinRing = index % RING_CAPACITY
      // Spread ties around the ring rather than stacking them on one bearing:
      // several premises can share a bearing to within a degree.
      const spread = (withinRing / RING_CAPACITY) * 0.55
      const angle = bearing + spread
      const radius = ring * RING_SPACING
      houses[house.id] = {
        gx: origin.gx + Math.cos(angle) * radius,
        gy: origin.gy + Math.sin(angle) * radius,
        gz: 0,
      }
    })
  })

  return { houses, transformers, geographic: true }
}

/** How many concentric rings a cluster of this size needs. */
function ringsFor(count: number): number {
  return Math.max(1, Math.ceil(count / RING_CAPACITY))
}

/** The original deterministic grid: rows of premises per transformer cluster.
 *  Used when the payload carries no coordinates. */
export function layoutSchematic(scene: ScenePayload): Omit<LayoutResult, 'geographic'> {
  const houses: Record<string, GridPoint> = {}
  const transformers: Record<string, GridPoint> = {}
  scene.transformers.forEach((transformer, clusterIndex) => {
    const cluster = scene.houses
      .filter((house) => house.transformer === transformer.id)
      .sort((a, b) => a.phase.localeCompare(b.phase) || a.id.localeCompare(b.id))
    const originX = (clusterIndex % 2) * 12
    const originY = Math.floor(clusterIndex / 2) * 9
    const rowLength = Math.max(1, Math.ceil(cluster.length / 3))
    transformers[transformer.id] = { gx: originX + rowLength / 2, gy: originY - 1.8, gz: 0 }
    cluster.forEach((house, index) => {
      const row = Math.floor(index / rowLength)
      const column = index % rowLength
      houses[house.id] = { gx: originX + column * 1.4, gy: originY + row * 1.7, gz: 0 }
    })
  })
  return { houses, transformers }
}

export function viewBoxFor(layout: LayoutResult) {
  const projected = [
    ...Object.values(layout.houses).map((point) => project(point.gx, point.gy)),
    ...Object.values(layout.transformers).map((point) => project(point.gx, point.gy)),
  ]
  if (projected.length === 0) return '0 0 100 100'
  const xs = projected.map((point) => point.x)
  const ys = projected.map((point) => point.y)
  const margin = TILE_SIZE * 3.2
  const minX = Math.min(...xs) - margin
  const minY = Math.min(...ys) - margin * 1.4
  return `${minX} ${minY} ${Math.max(...xs) - minX + margin} ${Math.max(...ys) - minY + margin * 1.6}`
}
