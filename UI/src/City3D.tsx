import { Line, OrbitControls, QuadraticBezierLine } from '@react-three/drei'
import { Canvas, useFrame } from '@react-three/fiber'
import { useMemo, useRef } from 'react'
import * as THREE from 'three'

import { layoutScene } from './layout'
import type { BlockPayload, SceneHouse, ScenePayload, TradePayload } from './types'

type Palette = Record<'ground' | 'raised' | 'rule' | 'ink' | 'quiet' | 'export' | 'import' | 'curtailed' | 'idle' | 'stress', string>

function paletteFromCss(): Palette {
  const css = getComputedStyle(document.documentElement)
  const read = (name: string) => css.getPropertyValue(name).trim()
  return {
    ground: read('--ground'), raised: read('--ground-raised'), rule: read('--rule'), ink: read('--ink'), quiet: read('--ink-quiet'),
    export: read('--export'), import: read('--import'), curtailed: read('--curtailed'), idle: read('--idle'), stress: read('--stress'),
  }
}

function ColonyHouse({ house, position, reading, palette, selected, onSelect }: {
  house: SceneHouse
  position: [number, number, number]
  reading: BlockPayload['houses'][string] | undefined
  palette: Palette
  selected: boolean
  onSelect: () => void
}) {
  const stateColor = reading?.state === 'export' ? palette.export : reading?.state === 'import' ? palette.import : palette.idle
  const magnitude = Math.min(1, Math.abs(reading?.net_kwh ?? 0) / 4)
  const height = house.kind === 'evhub' ? 1.1 : 0.72 + (Number(house.id.replace(/\D/g, '')) % 3) * 0.16
  const batteryLevel = Math.max(0.05, reading?.soc_frac ?? 0)
  return <group position={position} onClick={(event) => { event.stopPropagation(); onSelect() }}>
    <mesh position={[0, height / 2, 0]} castShadow receiveShadow>
      <boxGeometry args={house.kind === 'evhub' ? [1.15, height, 1.35] : [0.92, height, 0.92]} />
      <meshStandardMaterial color={palette.raised} roughness={0.72} emissive={selected ? palette.import : palette.ground} emissiveIntensity={selected ? 0.28 : 0} />
    </mesh>
    <mesh position={[0, height + 0.18, 0]} rotation={[0, Math.PI / 4, 0]} castShadow>
      <coneGeometry args={[house.kind === 'evhub' ? 1 : 0.72, 0.38, 4]} />
      <meshStandardMaterial color={stateColor} transparent opacity={0.48 + magnitude * 0.52} roughness={0.6} />
    </mesh>
    {house.has_pv && <mesh position={[0.13, height + 0.39, 0.02]} rotation={[-Math.PI / 2.9, 0, -Math.PI / 4]}>
      <boxGeometry args={[0.48, 0.035, 0.36]} /><meshStandardMaterial color={palette.import} metalness={0.55} roughness={0.3} />
    </mesh>}
    {house.has_battery && <group position={[0.67, 0, 0.4]}>
      <mesh position={[0, 0.34, 0]}><boxGeometry args={[0.22, 0.68, 0.25]} /><meshStandardMaterial color={palette.rule} /></mesh>
      <mesh position={[0, batteryLevel * 0.31 + 0.03, 0.135]}><boxGeometry args={[0.14, batteryLevel * 0.58, 0.025]} /><meshStandardMaterial color={palette.export} emissive={palette.export} emissiveIntensity={0.18} /></mesh>
    </group>}
    {(reading?.curtailed ?? 0) > 0 && <mesh position={[0, 0.04, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      <ringGeometry args={[0.75, 0.84, 32]} /><meshBasicMaterial color={palette.curtailed} transparent opacity={0.9} side={THREE.DoubleSide} />
    </mesh>}
  </group>
}

function Transformer3D({ id, position, reading, palette }: { id: string; position: [number, number, number]; reading: BlockPayload['transformers'][string] | undefined; palette: Palette }) {
  const load = Math.min(1, reading?.loading ?? 0)
  const color = reading?.stressed ? palette.stress : palette.import
  return <group position={position}>
    <mesh position={[0, 0.72, 0]} castShadow><boxGeometry args={[1.2, 1.35, 1.2]} /><meshStandardMaterial color={reading?.stressed ? palette.stress : palette.idle} roughness={0.52} /></mesh>
    <mesh position={[-0.42, 1.55, 0]} rotation={[0, 0, Math.PI / 2]}><cylinderGeometry args={[0.22, 0.22, 0.7, 16]} /><meshStandardMaterial color={palette.raised} metalness={0.4} /></mesh>
    <mesh position={[0.42, 1.55, 0]} rotation={[0, 0, Math.PI / 2]}><cylinderGeometry args={[0.22, 0.22, 0.7, 16]} /><meshStandardMaterial color={palette.raised} metalness={0.4} /></mesh>
    <mesh position={[0, 0.05, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      <ringGeometry args={[0.98, 1.08, 48, 1, 0, Math.PI * 2 * load]} /><meshBasicMaterial color={color} side={THREE.DoubleSide} />
    </mesh>
    <pointLight color={color} intensity={reading?.stressed ? 7 : 2} distance={4} position={[0, 1.2, 0]} />
  </group>
}

function TradePulse({ trade, source, target, palette, index, reducedMotion }: { trade: TradePayload; source: THREE.Vector3; target: THREE.Vector3; palette: Palette; index: number; reducedMotion: boolean }) {
  const pulse = useRef<THREE.Mesh>(null)
  const curve = useMemo(() => {
    const midpoint = source.clone().lerp(target, 0.5)
    midpoint.y += Math.max(1.5, source.distanceTo(target) * 0.22)
    return new THREE.QuadraticBezierCurve3(source, midpoint, target)
  }, [source, target])
  useFrame(({ clock }) => {
    if (!pulse.current || reducedMotion) return
    const t = (clock.elapsedTime * 0.72 - index * 0.08 + 10) % 1
    pulse.current.position.copy(curve.getPoint(t))
  })
  const middle = curve.getPoint(0.5)
  return <group>
    <QuadraticBezierLine start={source} end={target} mid={middle} color={trade.curtailed ? palette.curtailed : palette.export} lineWidth={trade.curtailed ? 1.2 : 1.55} dashed={trade.curtailed > 0} dashScale={8} transparent opacity={0.48} />
    {!reducedMotion && <mesh ref={pulse}><sphereGeometry args={[0.09 + Math.min(trade.kwh, 3) * 0.015, 10, 10]} /><meshBasicMaterial color={trade.curtailed ? palette.curtailed : palette.import} /></mesh>}
  </group>
}

function NetworkScene({ scene, block, selected, onSelect }: { scene: ScenePayload; block: BlockPayload | null; selected: string | null; onSelect: (id: string | null) => void }) {
  const palette = useMemo(paletteFromCss, [])
  const layout = useMemo(() => layoutScene(scene), [scene])
  const reducedMotion = useMemo(() => matchMedia('(prefers-reduced-motion: reduce)').matches, [])
  const allPoints = [...Object.values(layout.houses), ...Object.values(layout.transformers)]
  const centerX = (Math.min(...allPoints.map((p) => p.gx)) + Math.max(...allPoints.map((p) => p.gx))) / 2
  const positionFor = (gx: number, gy: number, y = 0): [number, number, number] => [(gx - centerX) * 1.42, y, (gy - 0.5) * 1.7]
  const housePositions = Object.fromEntries(Object.entries(layout.houses).map(([id, p]) => [id, positionFor(p.gx, p.gy)])) as Record<string, [number, number, number]>
  const transformerPositions = Object.fromEntries(Object.entries(layout.transformers).map(([id, p]) => [id, positionFor(p.gx, p.gy)])) as Record<string, [number, number, number]>
  const largestTrades = [...(block?.trades ?? [])].sort((a, b) => b.kwh - a.kwh).slice(0, 12)
  return <>
    <color attach="background" args={[palette.ground]} /><fog attach="fog" args={[palette.ground, 32, 72]} />
    <ambientLight intensity={0.75} /><directionalLight position={[12, 28, 14]} intensity={2.6} color={palette.ink} castShadow shadow-mapSize={[2048, 2048]} />
    <pointLight position={[-18, 8, -5]} color={palette.export} intensity={13} distance={28} />
    <group onPointerMissed={() => onSelect(null)}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.04, 0]} receiveShadow><planeGeometry args={[58, 24]} /><meshStandardMaterial color={palette.ground} roughness={1} /></mesh>
      {[-2.5, 2.55].map((z) => <mesh key={z} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.005, z]} receiveShadow><planeGeometry args={[57, 1.15]} /><meshStandardMaterial color={palette.raised} roughness={1} /></mesh>)}
      <gridHelper args={[58, 48, palette.rule, palette.rule]} position={[0, 0.01, 0]} />
      {scene.houses.map((house) => {
        const from = transformerPositions[house.transformer], to = housePositions[house.id]
        return <Line key={`wire-${house.id}`} points={[[from[0], 0.45, from[2]], [to[0], 0.18, to[2]]]} color={block?.houses[house.id]?.state === 'export' ? palette.export : block?.houses[house.id]?.state === 'import' ? palette.import : palette.rule} lineWidth={0.6} transparent opacity={0.42} />
      })}
      {scene.houses.map((house) => <ColonyHouse key={house.id} house={house} position={housePositions[house.id]} reading={block?.houses[house.id]} palette={palette} selected={selected === house.id} onSelect={() => onSelect(house.id)} />)}
      {scene.transformers.map((transformer) => <Transformer3D key={transformer.id} id={transformer.id} position={transformerPositions[transformer.id]} reading={block?.transformers[transformer.id]} palette={palette} />)}
      {largestTrades.map((trade, index) => {
        const from = housePositions[trade.from], to = housePositions[trade.to]
        if (!from || !to) return null
        return <TradePulse key={`${block?.block}-${trade.from}-${trade.to}-${index}`} trade={trade} source={new THREE.Vector3(from[0], 1.25, from[2])} target={new THREE.Vector3(to[0], 1.25, to[2])} palette={palette} index={index} reducedMotion={reducedMotion} />
      })}
    </group>
    <OrbitControls makeDefault target={[0, 0, 0]} minDistance={18} maxDistance={62} minPolarAngle={0.65} maxPolarAngle={1.35} enableDamping dampingFactor={0.08} />
  </>
}

export function City3D(props: { scene: ScenePayload; block: BlockPayload | null; selected: string | null; onSelect: (id: string | null) => void }) {
  return <Canvas className="city-canvas" shadows dpr={[1, 1.5]} camera={{ position: [24, 24, 31], fov: 42, near: 0.1, far: 120 }} gl={{ antialias: true, powerPreference: 'high-performance' }}>
    <NetworkScene {...props} />
  </Canvas>
}
