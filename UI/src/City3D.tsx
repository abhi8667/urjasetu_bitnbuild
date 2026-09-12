import { Html, Line, OrbitControls, QuadraticBezierLine } from '@react-three/drei'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'

import { layoutScene } from './layout'
import type { BlockPayload, SceneHouse, ScenePayload, TradePayload } from './types'

type Point = [number, number, number]

export type CameraMode = 'orbit' | 'top-down' | 'perspective'

type CityProps = {
  scene: ScenePayload
  block: BlockPayload | null
  selected: string | null
  onSelect: (id: string | null) => void
  cameraMode?: CameraMode
  onCameraModeChange?: (mode: CameraMode) => void
  isNightMode?: boolean
}

type Part = { position: Point; scale: Point; rotation?: Point; house?: string }
type Batch = {
  key: string
  color: string
  parts: Part[]
  shape: 'box' | 'leaf' | 'trunk'
  materialType?: 'standard' | 'glass' | 'solar' | 'metal' | 'lawn' | 'road'
}

// High-Contrast Twilight Architectural Palette matching Image 1
const NIGHT_PALETTE = {
  background: '#101726',   // Luminous twilight sky navy
  ground: '#0c121e',       // Surrounding peripheral terrain
  baseplate: '#161e2b',    // Elevated neighborhood platform
  lawn: '#223d30',         // Rich dusk turf green - clearly distinct from road!
  road: '#273243',         // Clear dark asphalt grey
  stripe: '#ffffff',       // Crisp white road markings
  curb: '#475569',         // Concrete curbs
  raised: '#334155',       // Foundation plinths & slabs
  
  // Building Walls (High Contrast! Clearly distinct from roofs, lawns, and sky)
  wall1: '#c8d3e2',        // Crisp architectural slate-white
  wall2: '#b8c5d6',        // Modern warm architectural grey
  wall3: '#d6deea',        // Bright modern stucco
  
  // Roofs (Dark architectural slate - distinct from light walls!)
  roof: '#2d3748',         // Charcoal slate roof
  parapet: '#4a5568',      // Distinct parapet cap
  metal: '#64748b',        // Railings and window frames
  door: '#78593d',         // Warm natural wood door
  doorCanopy: '#e2e8f0',   // White entry porch
  
  glass: '#ffe8a3',        // Warm incandescent glowing windows
  solarMount: '#1e293b',   // Dark metal mounting frame
  solarCell: '#0284c7',    // Vibrant glowing deep blue PV
  hvac: '#475569',         // Mechanical rooftop units
  batteryBody: '#334155',  // LiFePO4 battery enclosure
  
  leafDark: '#1c4532',     // Lush dark forest foliage
  leafLight: '#276347',    // Lush medium green canopy
  trunk: '#4a3828',        // Warm wood trunk
  
  exportGold: '#ffb703',   // Gold P2P energy flow
  importCyan: '#00f0ff',   // Cyan grid/transformer glow
  stressRed: '#ef4444',    // Overload alert
}

/**
 * Storey height from what the premises IS, not from its meter number.
 *
 * This used to be `1.1 + (digits of house.id % 4) * 0.48` — the building's
 * height was a hash of its meter id. That happened to look varied against the
 * fixture's sequential H-01..H-60, but the registry's real ids are numeric
 * strings in blocks (10000-, 20000-, 30000-), so the hash collapses: whole
 * transformers come out the same height, and an apartment block renders the
 * same as a bungalow.
 *
 * `building_type` is in the registry and now travels with the scene, so an
 * apartment is tall, a commercial unit is broad and mid-rise, and a house is
 * low. Rooftop PV capacity adds a little on top, so a 6 kWp roof reads as a
 * bigger building than a 1.5 kWp one.
 */
const buildingHeight = (house: SceneHouse) => {
  if (house.kind === 'evhub' || house.building_type === 'evhub') return 1.25
  const base =
    house.building_type === 'apt' ? 2.35 : house.building_type === 'com' ? 1.75 : 1.15
  // Deterministic per-premises variation so a terrace is not a flat wall, keyed
  // off the surveyed distance from the transformer rather than the id.
  const jitter = ((Math.round((house.distance_m ?? 0) * 7) % 5) / 5) * 0.35
  const pvBonus = Math.min(0.4, (house.pv_kw ?? 0) * 0.05)
  return base + jitter + pvBonus
}

function cityLayout(scene: ScenePayload) {
  const layout = layoutScene(scene)
  const points = [...Object.values(layout.houses), ...Object.values(layout.transformers)]
  const minX = Math.min(...points.map((p) => p.gx)), maxX = Math.max(...points.map((p) => p.gx))
  const minY = Math.min(...points.map((p) => p.gy)), maxY = Math.max(...points.map((p) => p.gy))
  const convert = (gx: number, gy: number): Point => [
    (gx - (minX + maxX) / 2) * 1.85,
    0,
    (gy - (minY + maxY) / 2) * 1.95,
  ]

  return {
    houses: Object.fromEntries(
      Object.entries(layout.houses).map(([id, p]) => [id, convert(p.gx, p.gy)])
    ) as Record<string, Point>,
    transformers: Object.fromEntries(
      Object.entries(layout.transformers).map(([id, p]) => [id, convert(p.gx, p.gy)])
    ) as Record<string, Point>,
    width: (maxX - minX) * 1.85 + 8,
    depth: (maxY - minY) * 1.95 + 8,
    discom: [(maxX - (minX + maxX) / 2) * 1.85 + 6.8, 0, (minY - (minY + maxY) / 2) * 1.95 - 4] as Point,
  }
}

// Build Complete Detailed Architecture with Balconies, Windows, Parapets, EV Hubs, PV Arrays
function buildDetailedArchitecture(scene: ScenePayload, layout: ReturnType<typeof cityLayout>): Batch[] {
  const batches = new Map<string, Batch>()

  const add = (
    color: string,
    position: Point,
    scale: Point,
    house?: string,
    shape: Batch['shape'] = 'box',
    rotation?: Point,
    materialType: Batch['materialType'] = 'standard'
  ) => {
    const key = `${shape}-${color}-${materialType}`
    if (!batches.has(key)) {
      batches.set(key, { key, color, shape, materialType, parts: [] })
    }
    batches.get(key)!.parts.push({ position, scale, house, rotation })
  }

  const tree = (x: number, z: number, scale = 1) => {
    add(NIGHT_PALETTE.trunk, [x, 0.45 * scale, z], [0.14 * scale, 0.9 * scale, 0.14 * scale], undefined, 'trunk')
    add(NIGHT_PALETTE.leafDark, [x, 1.15 * scale, z], [0.7 * scale, 0.88 * scale, 0.7 * scale], undefined, 'leaf')
    add(NIGHT_PALETTE.leafLight, [x + 0.2 * scale, 1.4 * scale, z + 0.05], [0.48 * scale, 0.6 * scale, 0.48 * scale], undefined, 'leaf')
  }

  const solarArray = (x: number, y: number, z: number, id?: string) => {
    // Tilted Mounting rack
    add(NIGHT_PALETTE.solarMount, [x, y, z], [1.12, 0.06, 0.88], id, 'box', [-0.18, 0, 0], 'metal')
    // 4x3 individual high-efficiency solar cells
    for (let col = 0; col < 4; col++) {
      for (let row = 0; row < 3; row++) {
        const pz = -0.28 + row * 0.28
        add(
          NIGHT_PALETTE.solarCell,
          [x - 0.42 + col * 0.28, y + 0.05 + pz * -0.18, z + pz],
          [0.25, 0.025, 0.25],
          id,
          'box',
          [-0.18, 0, 0],
          'solar'
        )
      }
    }
  }

  // Iterate over every house / premise
  scene.houses.forEach((house, index) => {
    const [x, , z] = layout.houses[house.id]
    const id = house.id
    const height = buildingHeight(house)
    const isHub = house.kind === 'evhub'

    // 1. Foundation Slab
    add(NIGHT_PALETTE.raised, [x, 0.04, z], [2.15, 0.12, 2.55], id)

    // EV Charging Hub Model
    if (isHub) {
      add(NIGHT_PALETTE.road, [x, 0.11, z], [2.05, 0.04, 2.35], id, 'box', undefined, 'road')
      // Steel canopy columns
      for (const sx of [-0.75, 0.75]) {
        add(NIGHT_PALETTE.metal, [x + sx, 0.72, z - 0.55], [0.09, 1.3, 0.09], id, 'box', undefined, 'metal')
      }
      // Illuminated Solar Canopy Roof
      add(NIGHT_PALETTE.raised, [x, 1.38, z - 0.22], [1.95, 0.12, 1.65], id)
      add(NIGHT_PALETTE.solarCell, [x, 1.45, z - 0.22], [1.85, 0.04, 1.55], id, 'box', undefined, 'solar')

      // Fast Charging Pedestals & parking bays
      for (const sx of [-0.5, 0.5]) {
        add(NIGHT_PALETTE.raised, [x + sx, 0.45, z - 0.75], [0.26, 0.68, 0.26], id)
        add(NIGHT_PALETTE.importCyan, [x + sx, 0.52, z - 0.61], [0.16, 0.26, 0.04], id, 'box', undefined, 'glass')
        // EV Bay pavement markings
        add(NIGHT_PALETTE.stripe, [x + sx, 0.14, z + 0.3], [0.03, 0.02, 1.35], id)
        // Parked EV Charging Body
        add(NIGHT_PALETTE.wall2, [x + sx, 0.32, z + 0.3], [0.38, 0.34, 0.85], id)
        add(NIGHT_PALETTE.glass, [x + sx, 0.53, z + 0.26], [0.32, 0.18, 0.4], id, 'box', undefined, 'glass')
      }
      return
    }

    // Residential House Architecture
    const wallColor =
      index % 3 === 0 ? NIGHT_PALETTE.wall1 : index % 3 === 1 ? NIGHT_PALETTE.wall2 : NIGHT_PALETTE.wall3

    // 2. Main Building Core Walls
    add(wallColor, [x, height / 2 + 0.12, z], [1.5, height, 1.56], id)

    // 3. Flat Rooftop Terrace Slab
    add(NIGHT_PALETTE.roof, [x, height + 0.16, z], [1.65, 0.14, 1.72], id)

    // 4. Parapet Walls around roof perimeter
    for (const sx of [-0.76, 0.76]) {
      add(wallColor, [x + sx, height + 0.32, z], [0.1, 0.26, 1.62], id)
    }
    add(wallColor, [x, height + 0.32, z - 0.76], [1.58, 0.26, 0.1], id)

    // 5. Ground Floor Entrance Door & Porch Awning
    add(NIGHT_PALETTE.door, [x + 0.36, 0.44, z + 0.79], [0.32, 0.65, 0.04], id)
    add(NIGHT_PALETTE.roof, [x + 0.36, 0.78, z + 0.95], [0.58, 0.08, 0.35], id)

    // 6. Multi-Floor Window Reveals with Metal Frames and Glowing Warm Glass
    for (let floor = 0.65; floor < height; floor += 0.56) {
      // Front Windows
      for (const sx of [-0.42, 0.1]) {
        // Metallic frame
        add(NIGHT_PALETTE.metal, [x + sx, floor, z + 0.79], [0.38, 0.36, 0.04], id, 'box', undefined, 'metal')
        // Warm glowing illuminated glass pane
        add(NIGHT_PALETTE.glass, [x + sx, floor, z + 0.81], [0.3, 0.28, 0.03], id, 'box', undefined, 'glass')
      }

      // Side Windows
      for (const sz of [-0.42, 0.26]) {
        add(NIGHT_PALETTE.glass, [x + 0.76, floor, z + sz], [0.03, 0.28, 0.32], id, 'box', undefined, 'glass')
        add(NIGHT_PALETTE.glass, [x - 0.76, floor, z + sz], [0.03, 0.28, 0.32], id, 'box', undefined, 'glass')
      }

      // 7. Projecting Cantilever Balcony for Upper Floors
      if (height > 1.5 && floor > 1.0) {
        // Balcony floor slab
        add(NIGHT_PALETTE.raised, [x, floor - 0.24, z + 0.96], [1.45, 0.08, 0.42], id)
        // Top handrail
        add(NIGHT_PALETTE.metal, [x, floor - 0.06, z + 1.14], [1.45, 0.05, 0.04], id, 'box', undefined, 'metal')
        // Vertical railing balusters
        for (const sx of [-0.65, 0, 0.65]) {
          add(NIGHT_PALETTE.metal, [x + sx, floor - 0.15, z + 1.14], [0.04, 0.2, 0.04], id, 'box', undefined, 'metal')
        }
      }
    }

    // 8. Rooftop Assets: Solar PV Array OR Utility HVAC Units
    if (house.has_pv) {
      solarArray(x, height + 0.42, z, id)
    } else {
      // Rooftop HVAC unit & water tank machinery
      add(NIGHT_PALETTE.hvac, [x - 0.32, height + 0.36, z - 0.2], [0.42, 0.34, 0.46], id)
      add(NIGHT_PALETTE.metal, [x - 0.32, height + 0.55, z - 0.2], [0.46, 0.06, 0.52], id, 'box', undefined, 'metal')
      add(NIGHT_PALETTE.hvac, [x + 0.35, height + 0.4, z - 0.3], [0.34, 0.32, 0.34], id, 'trunk')
    }

    // 9. Home BESS Battery Storage Unit
    if (house.has_battery) {
      add(NIGHT_PALETTE.batteryBody, [x + 0.94, 0.48, z + 0.36], [0.28, 0.72, 0.44], id)
      // LED Status Screen
      add(NIGHT_PALETTE.importCyan, [x + 1.08, 0.52, z + 0.36], [0.03, 0.26, 0.28], id, 'box', undefined, 'glass')
    }

    // 10. Front Garden Lawn & Landscaping Hedges
    add(NIGHT_PALETTE.lawn, [x - 0.38, 0.12, z - 1.1], [1.25, 0.07, 0.38], id, 'box', undefined, 'lawn')
    add(index % 2 ? NIGHT_PALETTE.leafDark : NIGHT_PALETTE.leafLight, [x - 0.32, 0.28, z - 1.1], [1.15, 0.25, 0.25], id, 'box')
  })

  // 11. Service Roads, Curbs, and Painted Center Stripes
  scene.transformers.forEach((transformer) => {
    const homes = scene.houses.filter((h) => h.transformer === transformer.id)
    const xs = homes.map((h) => layout.houses[h.id][0])
    const zs = homes.map((h) => layout.houses[h.id][2])
    const left = Math.min(...xs) - 1.2, right = Math.max(...xs) + 1.2

    for (const z of [...new Set(zs)]) {
      add(NIGHT_PALETTE.road, [(left + right) / 2, -0.02, z + 1.5], [right - left, 0.07, 0.48], undefined, 'box', undefined, 'road')
    }
    const tz = layout.transformers[transformer.id][2]
    add(NIGHT_PALETTE.lawn, [(left + right) / 2, -0.01, tz], [right - left, 0.08, 2.9], undefined, 'box', undefined, 'lawn')
    for (const x of [left + 0.6, right - 0.6]) {
      tree(x, tz, 0.95)
    }
  })

  // Main Crossroad Intersections with Center Stripes
  add(NIGHT_PALETTE.road, [0, -0.04, 0], [2.3, 0.1, layout.depth], undefined, 'box', undefined, 'road')
  add(NIGHT_PALETTE.road, [0, -0.035, 0.08], [layout.width, 0.1, 1.8], undefined, 'box', undefined, 'road')

  // Painted dashed road markings
  for (let z = -layout.depth / 2 + 1.5; z < layout.depth / 2; z += 1.5) {
    add(NIGHT_PALETTE.stripe, [0, 0.02, z], [0.05, 0.012, 0.7])
  }
  for (let x = -layout.width / 2 + 1.5; x < layout.width / 2; x += 1.5) {
    add(NIGHT_PALETTE.stripe, [x, 0.02, 0.08], [0.7, 0.012, 0.05])
  }

  // Periphery Trees
  for (let x = -layout.width / 2 + 1.5; x < layout.width / 2; x += 3.5) {
    tree(x, layout.depth / 2 - 0.9, 0.9)
    tree(x, -layout.depth / 2 + 0.9, 0.85)
  }

  return [...batches.values()]
}

// Instanced Mesh Renderer for Architecture
function ArchitectureBatch({
  batch,
  onSelect,
}: {
  batch: Batch
  onSelect: CityProps['onSelect']
}) {
  const mesh = useRef<THREE.InstancedMesh>(null)

  useLayoutEffect(() => {
    if (!mesh.current) return
    const dummy = new THREE.Object3D()
    batch.parts.forEach((part, i) => {
      dummy.position.set(...part.position)
      dummy.scale.set(...part.scale)
      dummy.rotation.set(...(part.rotation ?? [0, 0, 0]))
      dummy.updateMatrix()
      mesh.current!.setMatrixAt(i, dummy.matrix)
    })
    mesh.current.instanceMatrix.needsUpdate = true
    mesh.current.computeBoundingSphere()
  }, [batch])

  // Custom materials tailored for night illumination
  const material = useMemo(() => {
    if (batch.materialType === 'glass') {
      // Warm glowing night windows
      return new THREE.MeshStandardMaterial({
        color: '#ffe5a3',
        emissive: '#ffb347',
        emissiveIntensity: 1.35,
        roughness: 0.2,
      })
    }
    if (batch.materialType === 'solar') {
      // Reflective blue glowing rooftop solar
      return new THREE.MeshStandardMaterial({
        color: '#0284c7',
        emissive: '#0369a1',
        emissiveIntensity: 0.85,
        metalness: 0.85,
        roughness: 0.18,
      })
    }
    if (batch.materialType === 'metal') {
      return new THREE.MeshStandardMaterial({
        color: batch.color,
        metalness: 0.75,
        roughness: 0.35,
      })
    }
    if (batch.materialType === 'road') {
      return new THREE.MeshStandardMaterial({
        color: batch.color,
        roughness: 0.85,
      })
    }
    if (batch.materialType === 'lawn') {
      return new THREE.MeshStandardMaterial({
        color: batch.color,
        roughness: 0.9,
      })
    }
    return new THREE.MeshStandardMaterial({
      color: batch.color,
      roughness: 0.75,
    })
  }, [batch])

  return (
    <instancedMesh
      ref={mesh}
      args={[undefined, undefined, batch.parts.length]}
      castShadow
      receiveShadow
      onClick={(event) => {
        const id = batch.parts[event.instanceId ?? -1]?.house
        if (id) {
          event.stopPropagation()
          onSelect(id)
        }
      }}
    >
      {batch.shape === 'box' ? (
        <boxGeometry />
      ) : batch.shape === 'leaf' ? (
        <icosahedronGeometry args={[1, 1]} />
      ) : (
        <cylinderGeometry args={[0.5, 0.5, 1, 8]} />
      )}
      <primitive object={material} attach="material" />
    </instancedMesh>
  )
}

// Active Energy Glow Pads on the ground for each house
function EnergyPads({
  scene,
  block,
  layout,
}: {
  scene: ScenePayload
  block: BlockPayload | null
  layout: ReturnType<typeof cityLayout>
}) {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const batteryHouses = useMemo(() => scene.houses.filter((h) => h.has_battery), [scene])

  useLayoutEffect(() => {
    if (!mesh.current) return
    const dummy = new THREE.Object3D()
    const color = new THREE.Color()

    scene.houses.forEach((house, index) => {
      const state = block?.houses[house.id]
      const [x, , z] = layout.houses[house.id]
      dummy.position.set(x, 0.16, z + 1.25)
      dummy.scale.set(1.75, 0.06, 0.06)
      dummy.updateMatrix()
      mesh.current!.setMatrixAt(index, dummy.matrix)

      const hex = state?.curtailed
        ? NIGHT_PALETTE.stressRed
        : state?.state === 'export'
        ? NIGHT_PALETTE.exportGold
        : state?.state === 'import'
        ? NIGHT_PALETTE.importCyan
        : '#334155'
      mesh.current!.setColorAt(index, color.set(hex))
    })

    batteryHouses.forEach((house, index) => {
      const [x, , z] = layout.houses[house.id]
      const level = Math.max(0.05, Math.min(1, block?.houses[house.id]?.soc_frac ?? 0.8))
      dummy.position.set(x + 1.08, 0.2 + level * 0.26, z + 0.36)
      dummy.scale.set(0.03, level * 0.52, 0.26)
      dummy.updateMatrix()
      mesh.current!.setMatrixAt(scene.houses.length + index, dummy.matrix)
      mesh.current!.setColorAt(scene.houses.length + index, color.set(NIGHT_PALETTE.importCyan))
    })

    mesh.current.instanceMatrix.needsUpdate = true
    if (mesh.current.instanceColor) mesh.current.instanceColor.needsUpdate = true
  }, [scene, block, layout, batteryHouses])

  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, scene.houses.length + batteryHouses.length]}>
      <boxGeometry />
      <meshBasicMaterial />
    </instancedMesh>
  )
}

// Low-poly Transmission Tower (DISCOM Substation)
function TransmissionTower({ position }: { position: Point }) {
  const [x, y, z] = position
  return (
    <group position={[x, y, z]}>
      <mesh position={[0, 0.15, 0]} receiveShadow>
        <boxGeometry args={[4.8, 0.3, 4.0]} />
        <meshStandardMaterial color="#1a222f" roughness={0.7} />
      </mesh>
      <mesh position={[-1.3, 0.75, -0.6]} castShadow>
        <boxGeometry args={[1.3, 0.9, 0.9]} />
        <meshStandardMaterial color="#2d3748" metalness={0.4} roughness={0.5} />
      </mesh>
      <mesh position={[-1.3, 0.75, 0.6]} castShadow>
        <boxGeometry args={[1.1, 0.9, 0.9]} />
        <meshStandardMaterial color="#2d3748" metalness={0.4} roughness={0.5} />
      </mesh>

      <pointLight position={[-1.3, 1.4, 0]} color="#38bdf8" intensity={1.8} distance={6} />
      <pointLight position={[1.2, 2.8, 0]} color="#f59e0b" intensity={2} distance={9} />

      {/* Main High-Voltage Pylon / Tower */}
      <group position={[1.0, 0, 0]}>
        <mesh position={[-0.7, 3.0, -0.7]} rotation={[0.07, 0, -0.07]}>
          <cylinderGeometry args={[0.04, 0.08, 6.0, 6]} />
          <meshStandardMaterial color="#64748b" metalness={0.7} roughness={0.3} />
        </mesh>
        <mesh position={[0.7, 3.0, -0.7]} rotation={[0.07, 0, 0.07]}>
          <cylinderGeometry args={[0.04, 0.08, 6.0, 6]} />
          <meshStandardMaterial color="#64748b" metalness={0.7} roughness={0.3} />
        </mesh>
        <mesh position={[-0.7, 3.0, 0.7]} rotation={[-0.07, 0, -0.07]}>
          <cylinderGeometry args={[0.04, 0.08, 6.0, 6]} />
          <meshStandardMaterial color="#64748b" metalness={0.7} roughness={0.3} />
        </mesh>
        <mesh position={[0.7, 3.0, 0.7]} rotation={[-0.07, 0, 0.07]}>
          <cylinderGeometry args={[0.04, 0.08, 6.0, 6]} />
          <meshStandardMaterial color="#64748b" metalness={0.7} roughness={0.3} />
        </mesh>
        <mesh position={[0, 4.4, 0]}>
          <boxGeometry args={[3.4, 0.14, 0.14]} />
          <meshStandardMaterial color="#94a3b8" metalness={0.8} roughness={0.2} />
        </mesh>
        <mesh position={[0, 5.5, 0]}>
          <boxGeometry args={[2.6, 0.14, 0.14]} />
          <meshStandardMaterial color="#94a3b8" metalness={0.8} roughness={0.2} />
        </mesh>
        {[-1.6, 0, 1.6].map((off, i) => (
          <mesh key={i} position={[off, 4.0, 0]}>
            <cylinderGeometry args={[0.05, 0.05, 0.75, 8]} />
            <meshStandardMaterial color="#38bdf8" emissive="#0284c7" emissiveIntensity={0.65} />
          </mesh>
        ))}
      </group>

      <Html position={[0, 4.8, 0]} center zIndexRange={[5, 0]} style={{ pointerEvents: 'none' }}>
        <div className="hud-3d-tag tag-discom">
          <span className="dot-discom" />
          Grid / DISCOM
        </div>
      </Html>
    </group>
  )
}

// Central Distribution Transformer (DT-3) with Glowing Cyan Ring
function CentralTransformer({
  id,
  position,
  reading,
}: {
  id: string
  position: Point
  reading: BlockPayload['transformers'][string] | undefined
}) {
  const [x, y, z] = position
  const ringRef = useRef<THREE.Mesh>(null)

  useFrame(({ clock }) => {
    if (ringRef.current) {
      const s = 1 + Math.sin(clock.elapsedTime * 2.2) * 0.025
      ringRef.current.scale.set(s, s, s)
    }
  })

  return (
    <group position={[x, y, z]}>
      <mesh position={[0, 0.1, 0]} receiveShadow>
        <cylinderGeometry args={[2.5, 2.6, 0.22, 40]} />
        <meshStandardMaterial color="#1a222e" roughness={0.7} />
      </mesh>

      {/* Glowing Neon Cyan Base Ring (Matches Image 1 & 2!) */}
      <mesh ref={ringRef} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.24, 0]}>
        <ringGeometry args={[2.1, 2.34, 64]} />
        <meshBasicMaterial color="#00f0ff" transparent opacity={0.9} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.23, 0]}>
        <ringGeometry args={[1.9, 2.5, 64]} />
        <meshBasicMaterial color="#00f0ff" transparent opacity={0.22} />
      </mesh>

      {/* Transformer Core Steel Tank */}
      <mesh position={[0, 0.9, 0]} castShadow receiveShadow>
        <boxGeometry args={[1.6, 1.35, 1.35]} />
        <meshStandardMaterial color="#334155" metalness={0.65} roughness={0.35} />
      </mesh>

      {/* Cooling Fins */}
      {[-0.9, 0.9].map((offX) => (
        <group key={offX} position={[offX, 0.9, 0]}>
          {Array.from({ length: 7 }, (_, i) => (
            <mesh key={i} position={[0, 0, -0.5 + i * 0.16]} castShadow>
              <boxGeometry args={[0.2, 1.0, 0.05]} />
              <meshStandardMaterial color="#1e293b" metalness={0.7} roughness={0.4} />
            </mesh>
          ))}
        </group>
      ))}

      {/* Bushing Terminals on top */}
      {[-0.45, 0, 0.45].map((offX, i) => (
        <group key={i} position={[offX, 1.75, 0]}>
          <mesh>
            <cylinderGeometry args={[0.07, 0.1, 0.48, 12]} />
            <meshStandardMaterial color="#94a3b8" metalness={0.8} roughness={0.2} />
          </mesh>
          <mesh position={[0, 0.26, 0]}>
            <sphereGeometry args={[0.09, 12, 12]} />
            <meshStandardMaterial color="#f59e0b" emissive="#d97706" emissiveIntensity={0.8} />
          </mesh>
        </group>
      ))}

      <pointLight position={[0, 1.3, 0]} color="#00f0ff" intensity={2.0} distance={8} />

      <Html position={[0, 2.8, 0]} center zIndexRange={[6, 0]} style={{ pointerEvents: 'none' }}>
        <div className="hud-3d-tag tag-transformer">
          <span className="dot-cyan" />
          {id}
          {reading && <span className="load-val">{Math.round(reading.loading * 100)}%</span>}
        </div>
      </Html>
    </group>
  )
}

// Glowing Golden Energy Transfer Arc
function GlowingEnergyArc({
  source,
  target,
  trade,
  index,
}: {
  source: Point
  target: Point
  trade: TradePayload
  index: number
}) {
  const pulseRef = useRef<THREE.Mesh>(null)

  const curve = useMemo(() => {
    const start = new THREE.Vector3(...source)
    const end = new THREE.Vector3(...target)
    const dist = start.distanceTo(end)
    const mid = start.clone().lerp(end, 0.5)
    mid.y += Math.max(2.0, Math.min(6.5, dist * 0.44))
    return new THREE.QuadraticBezierCurve3(start, mid, end)
  }, [source, target])

  useFrame(({ clock }) => {
    if (pulseRef.current) {
      const t = (clock.elapsedTime * 0.42 + index * 0.15) % 1
      pulseRef.current.position.copy(curve.getPoint(t))
    }
  })

  const arcColor = trade.curtailed ? '#ef4444' : '#ffb703'

  return (
    <group>
      <QuadraticBezierLine
        start={curve.v0}
        end={curve.v2}
        mid={curve.v1}
        color={arcColor}
        lineWidth={2.5}
        transparent
        opacity={0.88}
      />
      <mesh ref={pulseRef}>
        <sphereGeometry args={[0.16, 12, 12]} />
        <meshBasicMaterial color="#ffffff" />
      </mesh>
    </group>
  )
}

// Streetlight fixtures
function StreetLight({ position }: { position: Point }) {
  const [x, y, z] = position
  return (
    <group position={[x, y, z]}>
      <mesh position={[0, 1.25, 0]}>
        <cylinderGeometry args={[0.04, 0.06, 2.5, 8]} />
        <meshStandardMaterial color="#475569" metalness={0.7} roughness={0.3} />
      </mesh>
      <mesh position={[0.22, 2.45, 0]} rotation={[0, 0, -0.4]}>
        <cylinderGeometry args={[0.03, 0.03, 0.52, 8]} />
        <meshStandardMaterial color="#475569" metalness={0.7} />
      </mesh>
      <mesh position={[0.42, 2.5, 0]}>
        <boxGeometry args={[0.24, 0.08, 0.13]} />
        <meshStandardMaterial color="#1e293b" />
      </mesh>
      <mesh position={[0.42, 2.45, 0]}>
        <boxGeometry args={[0.2, 0.03, 0.1]} />
        <meshBasicMaterial color="#ffeedd" />
      </mesh>
      <pointLight position={[0.42, 2.25, 0]} color="#ffcc66" intensity={1.3} distance={7} decay={2} />
    </group>
  )
}

// Camera Traversal Controller (Orbit / Top-Down / Perspective)
function CameraRig({
  mode,
  selectedFocus,
  extent,
}: {
  mode: CameraMode
  selectedFocus: Point | null
  /** Largest world-space dimension of the laid-out street. */
  extent: number
}) {
  const controlsRef = useRef<any>(null)
  const { camera } = useThree()

  useEffect(() => {
    if (!controlsRef.current) return
    // Frame whatever the layout produced instead of a constant.
    //
    // Every camera position below was tuned by hand against the old schematic
    // layout, whose extent was a fixed ~37 world units because it was a grid of
    // rows. The real surveyed layout has a different extent and a different
    // aspect, so the hand-tuned numbers put the whole street outside the
    // frustum — the 3D view came back showing roads and streetlights and not
    // one building. `k` rescales them to the layout actually in hand.
    // Distance that actually fits `extent` in a 42-degree frustum, rather than
    // a hand-tuned magnitude: half the extent over tan(fov/2), plus a margin so
    // the street is not flush against the edges. Placing the camera along the
    // same unit direction as before keeps each view's ANGLE, which was the part
    // of the original tuning worth preserving.
    const fit = (extent / 2) / Math.tan((42 * Math.PI) / 180 / 2)
    const along = (dir: [number, number, number], distance: number): [number, number, number] => {
      const length = Math.hypot(dir[0], dir[1], dir[2]) || 1
      return [
        (dir[0] / length) * distance,
        (dir[1] / length) * distance,
        (dir[2] / length) * distance,
      ]
    }

    if (mode === 'top-down') {
      camera.position.set(0, Math.max(30, fit * 1.05), 0.01)
      controlsRef.current.target.set(0, 0, 0)
      controlsRef.current.maxPolarAngle = 0.05
      controlsRef.current.minPolarAngle = 0
      controlsRef.current.enableRotate = false
    } else if (mode === 'perspective') {
      camera.position.set(...along([16, 9, 24], Math.max(24, fit * 1.15)))
      controlsRef.current.target.set(0, 1.2, 0)
      controlsRef.current.maxPolarAngle = Math.PI / 2 - 0.05
      controlsRef.current.minPolarAngle = 0.2
      controlsRef.current.enableRotate = true
    } else {
      if (selectedFocus) {
        camera.position.set(selectedFocus[0] + 12, 14, selectedFocus[2] + 14)  // focus is absolute, not scaled
        controlsRef.current.target.set(selectedFocus[0], 0.8, selectedFocus[2])
      } else {
        camera.position.set(...along([22, 24, 26], Math.max(28, fit * 1.2)))
        controlsRef.current.target.set(0, 0, 0)
      }
      controlsRef.current.maxPolarAngle = 1.35
      controlsRef.current.minPolarAngle = 0.1
      controlsRef.current.enableRotate = true
    }
    controlsRef.current.update()
  }, [mode, selectedFocus, camera, extent])

  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      enableDamping
      dampingFactor={0.06}
      minDistance={6}
      maxDistance={Math.max(85, extent * 2.6)}
      enablePan={true}
    />
  )
}

// Main 3D World Scene
function CityScene({
  scene,
  block,
  selected,
  onSelect,
  cameraMode,
}: CityProps & { cameraMode: CameraMode }) {
  const layout = useMemo(() => cityLayout(scene), [scene])
  const batches = useMemo(() => buildDetailedArchitecture(scene, layout), [scene, layout])
  const selectedPoint = selected ? layout.houses[selected] : null

  const activeTrades = useMemo(() => {
    return (block?.trades ?? []).slice(0, 16)
  }, [block])

  const streetlights = useMemo(() => {
    const list: Point[] = []
    const xs = [-6.2, 0, 6.2]
    const zs = [-8.5, -2, 4.5, 10.5]
    for (const x of xs) {
      for (const z of zs) {
        list.push([x + 1.2, 0, z])
      }
    }
    return list
  }, [])

  return (
    <>
      <CameraRig
        mode={cameraMode}
        selectedFocus={selectedPoint}
        extent={Math.max(layout.width, layout.depth)}
      />

      {/* Luminous Twilight Atmosphere */}
      <color attach="background" args={[NIGHT_PALETTE.background]} />
      <fog attach="fog" args={[NIGHT_PALETTE.background, 55, 145]} />

      {/* Sky/Ground Hemisphere illumination ensures buildings and ground are clearly visible */}
      <hemisphereLight args={['#8ea9d4', '#263b32', 2.2]} />
      <ambientLight color="#4b6282" intensity={1.2} />

      {/* Crisp Directional Moonlight */}
      <directionalLight
        position={[-22, 40, -18]}
        intensity={2.8}
        color="#e2efff"
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-35}
        shadow-camera-right={35}
        shadow-camera-top={35}
        shadow-camera-bottom={-35}
        shadow-bias={-0.0004}
      />
      {/* Warm Golden Urban Glow & Rim Light */}
      <directionalLight position={[28, 24, 28]} intensity={1.3} color="#fed7aa" />

      {/* Ground plane (Surrounding dark terrain) */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.06, 0]} receiveShadow>
        <planeGeometry args={[160, 160]} />
        <meshStandardMaterial color={NIGHT_PALETTE.ground} roughness={0.9} />
      </mesh>

      {/* Neighborhood base plate (Clean elevated urban podium) */}
      <mesh position={[0, -0.15, 0]} receiveShadow>
        <boxGeometry args={[layout.width + 4, 0.25, layout.depth + 4]} />
        <meshStandardMaterial color={NIGHT_PALETTE.baseplate} roughness={0.8} />
      </mesh>

      {/* DISCOM / High-Voltage Substation */}
      <TransmissionTower position={layout.discom} />

      {/* Feeder line from DISCOM to Central Transformer DT-3 */}
      {scene.transformers[0] && (
        <Line
          points={[
            [layout.discom[0] + 1.0, 4.4, layout.discom[2]],
            [
              (layout.discom[0] + layout.transformers[scene.transformers[0].id][0]) / 2,
              5.6,
              (layout.discom[2] + layout.transformers[scene.transformers[0].id][2]) / 2,
            ],
            [
              layout.transformers[scene.transformers[0].id][0],
              1.8,
              layout.transformers[scene.transformers[0].id][2],
            ],
          ]}
          color="#38bdf8"
          lineWidth={1.5}
          transparent
          opacity={0.65}
        />
      )}

      {/* Detailed Batched Architecture (Balconies, Windows, Parapets, Rooftop Arrays, EV Hubs, Roads) */}
      {batches.map((batch) => (
        <ArchitectureBatch key={batch.key} batch={batch} onSelect={onSelect} />
      ))}

      {/* Active Energy Pads on the ground for houses & batteries */}
      <EnergyPads scene={scene} block={block} layout={layout} />

      {/* Central Transformers */}
      {scene.transformers.map((transformer) => (
        <CentralTransformer
          key={transformer.id}
          id={transformer.id}
          position={layout.transformers[transformer.id]}
          reading={block?.transformers[transformer.id]}
        />
      ))}

      {/* Streetlights */}
      {streetlights.map((pos, i) => (
        <StreetLight key={i} position={pos} />
      ))}

      {/* Selection indicator & label */}
      {selectedPoint && (
        <group position={selectedPoint}>
          <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.22, 0]}>
            <ringGeometry args={[1.55, 1.72, 48]} />
            <meshBasicMaterial color="#00f0ff" />
          </mesh>
          <Html position={[0, 3.8, 0]} center zIndexRange={[10, 0]} style={{ pointerEvents: 'none' }}>
            <div className="hud-3d-tag tag-house-selected">
              <strong>{selected}</strong>
              <span>{block?.houses[selected!] ? `${Math.abs(block.houses[selected!].net_kwh).toFixed(1)} kWh` : 'Active'}</span>
            </div>
          </Html>
        </group>
      )}

      {/* Golden Glowing Energy Arcs */}
      {activeTrades.map((trade, index) => {
        const fromPos = layout.houses[trade.from]
        const toPos = layout.houses[trade.to]
        if (!fromPos || !toPos) return null

        const sourceHouse = scene.houses.find((h) => h.id === trade.from)!
        const targetHouse = scene.houses.find((h) => h.id === trade.to)!

        const sourceCoord: Point = [
          fromPos[0],
          buildingHeight(sourceHouse) + 0.6,
          fromPos[2],
        ]
        const targetCoord: Point = [
          toPos[0],
          buildingHeight(targetHouse) + 0.6,
          toPos[2],
        ]

        return (
          <GlowingEnergyArc
            key={`${trade.from}-${trade.to}-${index}`}
            source={sourceCoord}
            target={targetCoord}
            trade={trade}
            index={index}
          />
        )
      })}
    </>
  )
}

export function City3D(props: CityProps) {
  const [internalCameraMode, setInternalCameraMode] = useState<CameraMode>('orbit')
  const cameraMode = props.cameraMode ?? internalCameraMode

  return (
    <div className="city-canvas-container">
      <Canvas
        className="city-canvas"
        shadows
        camera={{ position: [24, 24, 26], fov: 42, near: 0.1, far: 300 }}
        gl={{ antialias: true, powerPreference: 'high-performance' }}
        onPointerMissed={() => props.onSelect(null)}
      >
        <CityScene {...props} cameraMode={cameraMode} />
      </Canvas>
    </div>
  )
}
