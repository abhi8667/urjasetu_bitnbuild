import { Html, Line, OrbitControls, QuadraticBezierLine } from '@react-three/drei'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'

import { layoutScene } from './layout'
import type { BlockPayload, SceneHouse, ScenePayload, TradePayload } from './types'

type Point = [number, number, number]
type Palette = Record<'ground' | 'raised' | 'rule' | 'ink' | 'quiet' | 'export' | 'import' | 'curtailed' | 'idle' | 'stress' | 'road' | 'lawn' | 'leaf' | 'leafLight' | 'trunk' | 'glass' | 'solar' | 'solarGrid' | 'wall' | 'wallWarm' | 'roof' | 'metal' | 'stripe', string>
type Part = { position: Point; scale: Point; rotation?: Point; house?: string }
type Batch = { color: string; parts: Part[]; shape: 'box' | 'leaf' | 'trunk' }
type CityProps = { scene: ScenePayload; block: BlockPayload | null; selected: string | null; onSelect: (id: string | null) => void }

function paletteFromCss(): Palette {
  const css = getComputedStyle(document.documentElement)
  const read = (name: string) => css.getPropertyValue(name).trim()
  return {
    ground: read('--map-ground'), raised: read('--ground-raised'), rule: read('--rule'), ink: read('--ink'), quiet: read('--ink-quiet'),
    export: read('--export'), import: read('--import'), curtailed: read('--curtailed'), idle: read('--idle'), stress: read('--stress'),
    road: read('--city-road'), lawn: read('--city-lawn'), leaf: read('--city-leaf'), leafLight: read('--city-leaf-light'), trunk: read('--city-trunk'),
    glass: read('--city-glass'), solar: read('--city-solar'), solarGrid: read('--city-solar-grid'), wall: read('--city-wall'), wallWarm: read('--city-wall-warm'),
    roof: read('--city-roof'), metal: read('--city-metal'), stripe: read('--city-stripe'),
  }
}

const buildingHeight = (house: SceneHouse) => house.kind === 'evhub' ? 1.15 : 1.05 + (Number(house.id.replace(/\D/g, '')) % 4) * 0.46

function cityLayout(scene: ScenePayload) {
  const layout = layoutScene(scene)
  const points = [...Object.values(layout.houses), ...Object.values(layout.transformers)]
  const minX = Math.min(...points.map(p => p.gx)), maxX = Math.max(...points.map(p => p.gx))
  const minY = Math.min(...points.map(p => p.gy)), maxY = Math.max(...points.map(p => p.gy))
  const convert = (gx: number, gy: number): Point => [(gx - (minX + maxX) / 2) * 1.65, 0, (gy - (minY + maxY) / 2) * 1.75]
  return {
    houses: Object.fromEntries(Object.entries(layout.houses).map(([id, p]) => [id, convert(p.gx, p.gy)])) as Record<string, Point>,
    transformers: Object.fromEntries(Object.entries(layout.transformers).map(([id, p]) => [id, convert(p.gx, p.gy)])) as Record<string, Point>,
    width: (maxX - minX) * 1.65 + 5.4,
    depth: (maxY - minY) * 1.75 + 5.5,
  }
}

// Architectural details are batched by material: hundreds of windows and solar cells
// use a handful of GPU draw calls, with instance IDs retaining house selection.
function buildArchitecture(scene: ScenePayload, layout: ReturnType<typeof cityLayout>, palette: Palette): Batch[] {
  const batches = new Map<string, Batch>()
  const add = (color: string, position: Point, scale: Point, house?: string, shape: Batch['shape'] = 'box', rotation?: Point) => {
    const key = `${shape}-${color}`
    if (!batches.has(key)) batches.set(key, { color, shape, parts: [] })
    batches.get(key)!.parts.push({ position, scale, house, rotation })
  }
  const tree = (x: number, z: number, scale = 1) => {
    add(palette.trunk, [x, .45 * scale, z], [.13 * scale, .9 * scale, .13 * scale], undefined, 'trunk')
    add(palette.leaf, [x, 1.12 * scale, z], [.65 * scale, .84 * scale, .65 * scale], undefined, 'leaf')
    add(palette.leafLight, [x + .2 * scale, 1.38 * scale, z + .04], [.44 * scale, .58 * scale, .44 * scale], undefined, 'leaf')
  }
  const solarArray = (x: number, y: number, z: number, id?: string) => {
    add(palette.metal, [x, y, z], [1.02, .06, .78], id, 'box', [-.17, 0, 0])
    for (let col = 0; col < 4; col++) for (let row = 0; row < 3; row++) {
      const pz = -.26 + row * .26
      add(palette.solar, [x - .375 + col * .25, y + .045 + pz * -.17, z + pz], [.235, .025, .24], id, 'box', [-.17, 0, 0])
    }
  }
  scene.houses.forEach((house, index) => {
    const [x, , z] = layout.houses[house.id]
    const id = house.id, height = buildingHeight(house), isHub = house.kind === 'evhub'
    add(palette.raised, [x, .035, z], [2.05, .12, 2.48], id)
    if (isHub) {
      add(palette.road, [x, .11, z], [1.95, .04, 2.25], id)
      for (const sx of [-.72, .72]) add(palette.metal, [x + sx, .69, z - .55], [.08, 1.2, .08], id)
      add(palette.import, [x, 1.34, z - .22], [1.85, .12, 1.55], id)
      for (const sx of [-.48, .48]) {
        add(palette.raised, [x + sx, .44, z - .73], [.25, .64, .24], id)
        add(palette.import, [x + sx, .5, z - .59], [.15, .25, .03], id)
        add(palette.stripe, [x + sx, .14, z + .28], [.025, .02, 1.28], id)
        add(palette.wallWarm, [x + sx, .32, z + .28], [.35, .32, .8], id)
        add(palette.glass, [x + sx, .52, z + .24], [.3, .16, .38], id)
      }
      return
    }
    const wall = index % 3 === 0 ? palette.wallWarm : index % 3 === 1 ? palette.wall : palette.raised
    add(wall, [x, height / 2 + .12, z], [1.42, height, 1.48], id)
    add(palette.roof, [x, height + .16, z], [1.57, .13, 1.63], id)
    // Flat terraces, parapets, front door, window reveals and projecting balconies.
    for (const sx of [-.72, .72]) add(wall, [x + sx, height + .31, z], [.09, .24, 1.54], id)
    add(wall, [x, height + .31, z - .72], [1.5, .24, .09], id)
    add(palette.trunk, [x + .34, .43, z + .754], [.3, .62, .035], id)
    add(palette.roof, [x + .34, .17, z + .92], [.55, .13, .33], id)
    for (let floor = .64; floor < height; floor += .55) {
      for (const sx of [-.4, .1]) {
        add(palette.metal, [x + sx, floor, z + .752], [.35, .34, .045], id)
        add(palette.glass, [x + sx, floor, z + .782], [.28, .27, .02], id)
      }
      for (const sz of [-.4, .25]) {
        add(palette.glass, [x + .72, floor, z + sz], [.025, .27, .3], id)
        add(palette.glass, [x - .72, floor, z + sz], [.025, .27, .3], id)
      }
      if (height > 1.5 && floor > 1) {
        add(palette.raised, [x, floor - .22, z + .91], [1.4, .07, .4], id)
        add(palette.metal, [x, floor - .06, z + 1.08], [1.4, .045, .035], id)
        for (const sx of [-.63, 0, .63]) add(palette.metal, [x + sx, floor - .14, z + 1.08], [.035, .19, .035], id)
      }
    }
    if (house.has_pv) solarArray(x, height + .44, z, id)
    else {
      add(palette.raised, [x - .3, height + .35, z - .2], [.4, .32, .45], id)
      add(palette.metal, [x - .3, height + .53, z - .2], [.45, .06, .5], id)
      add(palette.idle, [x + .34, height + .38, z - .3], [.32, .3, .32], id, 'trunk')
    }
    if (house.has_battery) {
      add(palette.raised, [x + .88, .46, z + .34], [.26, .7, .42], id)
      add(palette.import, [x + 1.02, .5, z + .34], [.02, .25, .27], id)
    }
    add(palette.lawn, [x - .37, .115, z - 1.04], [1.22, .07, .35], id)
    add(index % 2 ? palette.leaf : palette.leafLight, [x - .3, .27, z - 1.04], [1.12, .24, .24], id)
  })
  // Service roads follow each row of premises, with curbs and marked center lines.
  scene.transformers.forEach(transformer => {
    const homes = scene.houses.filter(house => house.transformer === transformer.id)
    const xs = homes.map(h => layout.houses[h.id][0]), zs = homes.map(h => layout.houses[h.id][2])
    const left = Math.min(...xs) - 1.1, right = Math.max(...xs) + 1.1
    for (const z of [...new Set(zs)]) {
      add(palette.road, [(left + right) / 2, -.025, z + 1.46], [right - left, .065, .43])
    }
    const tz = layout.transformers[transformer.id][2]
    add(palette.lawn, [(left + right) / 2, -.01, tz], [right - left, .08, 2.7])
    for (const x of [left + .55, right - .55]) tree(x, tz, .9)
  })
  const crossX = 0
  add(palette.road, [crossX, -.045, 0], [2.15, .1, layout.depth - .1])
  add(palette.road, [0, -.04, .08], [layout.width - .1, .1, 1.65])
  for (let z = -layout.depth / 2 + 1; z < layout.depth / 2; z += 1.4) add(palette.stripe, [crossX, .018, z], [.045, .012, .65])
  for (let x = -layout.width / 2 + 1; x < layout.width / 2; x += 1.4) add(palette.stripe, [x, .018, .08], [.65, .012, .045])
  for (const direction of [-1, 1]) for (let i = 0; i < 6; i++) {
    add(palette.stripe, [crossX - .73 + i * .28, .02, direction * 1.55], [.16, .015, .64])
    add(palette.stripe, [direction * 1.8, .02, -.52 + i * .24], [.65, .015, .12])
  }
  for (let x = -layout.width / 2 + 1; x < layout.width / 2; x += 3.2) {
    tree(x, layout.depth / 2 - .85, .85)
    tree(x, -layout.depth / 2 + .85, .8)
  }
  // Streetlights and a few parked cars provide scale without simulated traffic.
  for (const z of [-8, -3, 4, 9]) {
    add(palette.metal, [crossX + 1.2, 1.05, z], [.065, 2.1, .065])
    add(palette.metal, [crossX + .95, 2.08, z], [.55, .07, .08])
    add(palette.stripe, [crossX + .7, 2.03, z], [.24, .055, .16])
    add(z > 0 ? palette.wallWarm : palette.import, [crossX - .55, .24, z + .6], [.5, .34, 1.02])
    add(palette.glass, [crossX - .55, .46, z + .56], [.44, .2, .48])
  }
  return [...batches.values()]
}

function ArchitectureBatch({ batch, onSelect }: { batch: Batch; onSelect: CityProps['onSelect'] }) {
  const mesh = useRef<THREE.InstancedMesh>(null)
  useLayoutEffect(() => {
    const dummy = new THREE.Object3D()
    batch.parts.forEach((part, i) => {
      dummy.position.set(...part.position); dummy.scale.set(...part.scale); dummy.rotation.set(...(part.rotation ?? [0, 0, 0])); dummy.updateMatrix()
      mesh.current!.setMatrixAt(i, dummy.matrix)
    })
    mesh.current!.instanceMatrix.needsUpdate = true
    mesh.current!.computeBoundingSphere()
  }, [batch])
  return <instancedMesh ref={mesh} args={[undefined, undefined, batch.parts.length]} castShadow receiveShadow
    onClick={event => { const id = batch.parts[event.instanceId ?? -1]?.house; if (id) { event.stopPropagation(); onSelect(id) } }}>
    {batch.shape === 'box' ? <boxGeometry /> : batch.shape === 'leaf' ? <icosahedronGeometry args={[1, 1]} /> : <cylinderGeometry args={[.5, .5, 1, 8]} />}
    <meshStandardMaterial color={batch.color} roughness={.78} />
  </instancedMesh>
}

function EnergyPads({ scene, block, layout, palette }: { scene: ScenePayload; block: BlockPayload | null; layout: ReturnType<typeof cityLayout>; palette: Palette }) {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const batteryHouses = useMemo(() => scene.houses.filter(house => house.has_battery), [scene])
  useLayoutEffect(() => {
    const dummy = new THREE.Object3D(), color = new THREE.Color()
    scene.houses.forEach((house, index) => {
      const state = block?.houses[house.id]
      const [x, , z] = layout.houses[house.id]
      dummy.position.set(x, .18, z + 1.22); dummy.scale.set(1.7, .065, .055); dummy.updateMatrix()
      mesh.current!.setMatrixAt(index, dummy.matrix)
      mesh.current!.setColorAt(index, color.set(state?.curtailed ? palette.curtailed : state?.state === 'export' ? palette.export : state?.state === 'import' ? palette.import : palette.idle))
    })
    batteryHouses.forEach((house, index) => {
      const [x, , z] = layout.houses[house.id]
      const level = Math.max(.01, Math.min(1, block?.houses[house.id]?.soc_frac ?? 0))
      dummy.position.set(x + 1.025, .2 + level * .26, z + .34)
      dummy.scale.set(.026, level * .52, .25); dummy.updateMatrix()
      mesh.current!.setMatrixAt(scene.houses.length + index, dummy.matrix)
      mesh.current!.setColorAt(scene.houses.length + index, color.set(palette.export))
    })
    mesh.current!.instanceMatrix.needsUpdate = true
    if (mesh.current!.instanceColor) mesh.current!.instanceColor.needsUpdate = true
    mesh.current!.computeBoundingSphere()
  }, [scene, block, layout, palette, batteryHouses])
  return <instancedMesh ref={mesh} args={[undefined, undefined, scene.houses.length + batteryHouses.length]}><boxGeometry /><meshBasicMaterial /></instancedMesh>
}

function Transformer3D({ id, position, reading, palette }: { id: string; position: Point; reading: BlockPayload['transformers'][string] | undefined; palette: Palette }) {
  const color = reading?.stressed ? palette.stress : palette.import
  return <group position={position}>
    <mesh position={[0, .08, 0]} receiveShadow><boxGeometry args={[2.65, .22, 2.35]} /><meshStandardMaterial color={palette.roof} /></mesh>
    <mesh position={[0, .72, 0]} castShadow><boxGeometry args={[1.12, 1.12, .95]} /><meshStandardMaterial color={palette.metal} metalness={.35} roughness={.48} /></mesh>
    {[-.65, .65].map(x => <group key={x} position={[x, .74, 0]}>{Array.from({ length: 6 }, (_, i) => <mesh key={i} position={[0, 0, -.4 + i * .16]} castShadow><boxGeometry args={[.16, .88, .065]} /><meshStandardMaterial color={palette.idle} metalness={.35} roughness={.6} /></mesh>)}</group>)}
    {[-.35, 0, .35].map(x => <group key={x} position={[x, 1.43, 0]}><mesh><cylinderGeometry args={[.07, .1, .4, 10]} /><meshStandardMaterial color={palette.trunk} /></mesh><mesh position={[0, .05, 0]}><torusGeometry args={[.1, .03, 6, 10]} /><meshStandardMaterial color={palette.roof} /></mesh></group>)}
    <mesh position={[0, .9, .49]}><boxGeometry args={[.23, .2, .025]} /><meshBasicMaterial color={palette.export} /></mesh>
    <mesh position={[0, .215, 1.06]}><boxGeometry args={[2.3, .055, .045]} /><meshBasicMaterial color={color} /></mesh>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, .21, 0]}><ringGeometry args={[1.52, 1.57, 64, 1, 0, 2 * Math.PI * Math.min(1, reading?.loading ?? 0)]} /><meshBasicMaterial color={color} transparent opacity={.75} /></mesh>
    <Html position={[0, 2.3, 0]} center zIndexRange={[2, 0]} style={{ pointerEvents: 'none' }}><div className={`map-label ${reading?.stressed ? 'stressed' : ''}`}><i />{id}<span>{reading ? `${Math.round(reading.loading * 100)}%` : '—'}</span></div></Html>
  </group>
}

function TradePulse({ trade, source, target, palette, index, reducedMotion }: { trade: TradePayload; source: Point; target: Point; palette: Palette; index: number; reducedMotion: boolean }) {
  const pulse = useRef<THREE.Mesh>(null)
  const curve = useMemo(() => {
    const start = new THREE.Vector3(...source), end = new THREE.Vector3(...target)
    const midpoint = start.clone().lerp(end, .5)
    midpoint.y += Math.max(2.2, start.distanceTo(end) * .3)
    return new THREE.QuadraticBezierCurve3(start, midpoint, end)
  }, [source, target])
  useFrame(({ clock }) => {
    if (pulse.current && !reducedMotion) pulse.current.position.copy(curve.getPoint((clock.elapsedTime * .32 + index * .11) % 1))
  })
  const color = trade.curtailed ? palette.curtailed : palette.export
  return <group>
    <QuadraticBezierLine start={curve.v0} end={curve.v2} mid={curve.v1} color={color} lineWidth={2} dashed={trade.curtailed > 0} dashScale={6} transparent opacity={.8} />
    {!reducedMotion && <mesh ref={pulse}><sphereGeometry args={[.13, 10, 8]} /><meshBasicMaterial color={palette.raised} /></mesh>}
  </group>
}

function CameraRig({ width, depth, topDown, reset, focus }: { width: number; depth: number; topDown: boolean; reset: number; focus: Point | null }) {
  const { camera, size } = useThree()
  useLayoutEffect(() => {
    const cam = camera as THREE.OrthographicCamera
    cam.position.set(...(topDown ? [0, 60, .001] : [28, 34, 32]) as Point)
    if (focus) cam.position.add(new THREE.Vector3(...focus))
    cam.lookAt(...(focus ?? [0, 0, 0]) as Point)
    cam.updateMatrixWorld()
    const points = [-1, 1].flatMap(x => [-1, 1].flatMap(z => [0, 5].map(y => new THREE.Vector3(x * width / 2, y, z * depth / 2).applyMatrix4(cam.matrixWorldInverse))))
    const extentX = Math.max(...points.map(p => p.x)) - Math.min(...points.map(p => p.x))
    const extentY = Math.max(...points.map(p => p.y)) - Math.min(...points.map(p => p.y))
    cam.zoom = focus ? Math.min(60, Math.min(size.width, size.height) / 7) : Math.min(size.width * .91 / extentX, size.height * .88 / extentY)
    cam.updateProjectionMatrix()
  }, [camera, size, width, depth, topDown, reset, focus])
  return <OrbitControls key={`${topDown}-${reset}-${focus?.join(',')}`} makeDefault target={focus ?? [0, 0, 0]} minZoom={3} maxZoom={65} minPolarAngle={.02} maxPolarAngle={1.25} enableRotate={!topDown} enablePan={false} enableDamping dampingFactor={.09} />
}

function NetworkScene({ scene, block, selected, onSelect, topDown, reset, energy, feeders, focus }: CityProps & { topDown: boolean; reset: number; energy: boolean; feeders: boolean; focus: boolean }) {
  const palette = useMemo(paletteFromCss, [])
  const layout = useMemo(() => cityLayout(scene), [scene])
  const batches = useMemo(() => buildArchitecture(scene, layout, palette), [scene, layout, palette])
  const [reducedMotion, setReducedMotion] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => { const query = matchMedia('(prefers-reduced-motion: reduce)'); const change = () => setReducedMotion(query.matches); query.addEventListener('change', change); return () => query.removeEventListener('change', change) }, [])
  const largestTrades = useMemo(() => [...(block?.trades ?? [])].sort((a, b) => b.kwh - a.kwh).slice(0, 12), [block])
  const selectedPosition = selected ? layout.houses[selected] : null
  return <>
    <CameraRig width={layout.width} depth={layout.depth} topDown={topDown} reset={reset} focus={focus && selectedPosition ? selectedPosition : null} />
    <color attach="background" args={[palette.ground]} />
    <hemisphereLight args={[palette.raised, palette.leaf, 1.1]} />
    <directionalLight position={[-12, 25, 8]} intensity={3} color={palette.stripe} castShadow shadow-mapSize={[2048, 2048]} shadow-camera-left={-30} shadow-camera-right={30} shadow-camera-top={30} shadow-camera-bottom={-30} shadow-normalBias={.025} shadow-bias={-.0002} shadow-radius={3} />
    <directionalLight position={[15, 8, -14]} intensity={.8} color={palette.raised} />
    <mesh position={[0, -.58, 0]} receiveShadow><boxGeometry args={[layout.width, 1, layout.depth]} /><meshStandardMaterial color={palette.metal} roughness={.85} /></mesh>
    <mesh position={[0, -.11, 0]} receiveShadow><boxGeometry args={[layout.width + .12, .15, layout.depth + .12]} /><meshStandardMaterial color={palette.roof} /></mesh>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -.015, 0]} receiveShadow><planeGeometry args={[layout.width - .1, layout.depth - .1]} /><meshStandardMaterial color={palette.lawn} /></mesh>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -.62, 0]} receiveShadow><planeGeometry args={[200, 200]} /><shadowMaterial transparent opacity={.14} /></mesh>
    {batches.map(batch => <ArchitectureBatch key={`${batch.shape}-${batch.color}`} batch={batch} onSelect={onSelect} />)}
    <EnergyPads scene={scene} block={block} layout={layout} palette={palette} />
    {feeders && scene.houses.map(house => {
      const from = layout.transformers[house.transformer], to = layout.houses[house.id]
      return <Line key={`wire-${house.id}`} points={[[from[0], .18, from[2]], [from[0], .18, to[2] - 1.3], [to[0], .18, to[2] - 1.3], [to[0], .18, to[2]]]} color={palette.import} lineWidth={1} transparent opacity={.5} />
    })}
    {scene.transformers.map(transformer => <Transformer3D key={transformer.id} id={transformer.id} position={layout.transformers[transformer.id]} reading={block?.transformers[transformer.id]} palette={palette} />)}
    {selectedPosition && <group position={selectedPosition}><mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, .2, 0]}><ringGeometry args={[1.1, 1.18, 48]} /><meshBasicMaterial color={palette.import} /></mesh><Html position={[0, 3.7, 0]} center zIndexRange={[3, 0]} style={{ pointerEvents: 'none' }}><div className="selected-map-label">{selected}</div></Html></group>}
    {energy && largestTrades.map((trade, index) => {
      const from = layout.houses[trade.from], to = layout.houses[trade.to]
      if (!from || !to) return null
      const sourceHouse = scene.houses.find(h => h.id === trade.from)!, targetHouse = scene.houses.find(h => h.id === trade.to)!
      return <TradePulse key={`${trade.from}-${trade.to}-${index}`} trade={trade} source={[from[0], buildingHeight(sourceHouse) + .6, from[2]]} target={[to[0], buildingHeight(targetHouse) + .6, to[2]]} palette={palette} index={index} reducedMotion={reducedMotion} />
    })}
  </>
}

export function City3D(props: CityProps) {
  const [topDown, setTopDown] = useState(false), [reset, setReset] = useState(0)
  const [energy, setEnergy] = useState(true), [feeders, setFeeders] = useState(false), [focus, setFocus] = useState(false)
  return <>
    <div className="scene-toolbar" role="group" aria-label="Camera controls">{props.selected && <button aria-pressed={focus} onClick={() => setFocus(value => !value)}>Focus home</button>}<button aria-pressed={!topDown} onClick={() => setTopDown(false)}>Perspective</button><button aria-pressed={topDown} onClick={() => setTopDown(true)}>Top view</button><button onClick={() => { setReset(value => value + 1); setFocus(false) }} aria-label="Reset camera view" title="Reset camera view">↺</button></div>
    <Canvas className="city-canvas" orthographic shadows dpr={[1, 1.75]} camera={{ position: [28, 34, 32], zoom: 12, near: .1, far: 200 }} gl={{ antialias: true, powerPreference: 'high-performance' }} onPointerMissed={() => props.onSelect(null)}>
      <NetworkScene {...props} topDown={topDown} reset={reset} energy={energy} feeders={feeders} focus={focus} />
    </Canvas>
    <div className="scene-layers" role="group" aria-label="Network layers"><button aria-pressed={energy} onClick={() => setEnergy(value => !value)}><i className="energy-layer-dot" />Energy flows</button><button aria-pressed={feeders} onClick={() => setFeeders(value => !value)}><i />Feeder lines</button></div>
  </>
}
