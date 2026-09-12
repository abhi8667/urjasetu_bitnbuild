import type { ScenePayload } from './types'

export interface GridPoint { gx: number; gy: number; gz: number }
export interface ScreenPoint { x: number; y: number }
export interface LayoutResult {
  houses: Record<string, GridPoint>
  transformers: Record<string, GridPoint>
}

const TILE_SIZE = 30
const COS30 = Math.cos(Math.PI / 6)
const SIN30 = 0.5

export function project(gx: number, gy: number, gz = 0): ScreenPoint {
  return {
    x: (gx - gy) * COS30 * TILE_SIZE,
    y: (gx + gy) * SIN30 * TILE_SIZE - gz * TILE_SIZE,
  }
}

export function layoutScene(scene: ScenePayload): LayoutResult {
  const houses: Record<string, GridPoint> = {}
  const transformers: Record<string, GridPoint> = {}
  scene.transformers.forEach((transformer, clusterIndex) => {
    const cluster = scene.houses
      .filter((house) => house.transformer === transformer.id)
      .sort((a, b) => a.phase.localeCompare(b.phase) || a.id.localeCompare(b.id))
    const originX = (clusterIndex % 2) * 12
    const originY = Math.floor(clusterIndex / 2) * 9
    const rowLength = Math.ceil(cluster.length / 3)
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
  const xs = projected.map((point) => point.x)
  const ys = projected.map((point) => point.y)
  const margin = TILE_SIZE * 3.2
  const minX = Math.min(...xs) - margin
  const minY = Math.min(...ys) - margin * 1.4
  return `${minX} ${minY} ${Math.max(...xs) - minX + margin} ${Math.max(...ys) - minY + margin * 1.6}`
}
