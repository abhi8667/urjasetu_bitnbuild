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
  let cursor = 0

  scene.transformers.forEach((transformer) => {
    const cluster = scene.houses
      .filter((house) => house.transformer === transformer.id)
      .sort((a, b) => a.phase.localeCompare(b.phase) || a.id.localeCompare(b.id))
    const rowLength = Math.ceil(cluster.length / 2)
    transformers[transformer.id] = { gx: cursor + Math.floor(rowLength / 2), gy: -2, gz: 0 }
    cluster.forEach((house, index) => {
      const row = index % 2
      const column = Math.floor(index / 2)
      houses[house.id] = { gx: cursor + column, gy: row * 3, gz: 0 }
    })
    cursor += rowLength + 2
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
