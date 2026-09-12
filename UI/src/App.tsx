import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Line, OrbitControls, Html, PerspectiveCamera } from '@react-three/drei'
import { Activity, AlertTriangle, Building2, ChevronRight, CircleGauge, Gauge, Layers3, MousePointer2, Pause, Play, RadioTower, RotateCcw, Search, Sun, X, Zap } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { buildings, citySummary, gridNodes, powerLines, type Building, type GridNode } from './cityData'

type Selectable = (Building & { kind: 'building' }) | GridNode

const nodeMap = new Map(gridNodes.map((node) => [node.id, node]))

function BuildingMesh({ building, selected, onSelect }: { building: Building; selected: boolean; onSelect: () => void }) {
  const height = building.floors * 0.72 + 0.6
  const color = building.feeder === 'North' ? '#56706a' : building.feeder === 'Central' ? '#6c7566' : '#676b62'
  return (
    <group position={[building.x, 0, building.z]}>
      <mesh
        position={[0, height / 2, 0]}
        castShadow
        receiveShadow
        onClick={(e) => { e.stopPropagation(); onSelect() }}
      >
        <boxGeometry args={[building.footprint[0], height, building.footprint[1]]} />
        <meshStandardMaterial color={selected ? '#f6c453' : color} roughness={0.82} emissive={selected ? '#b07110' : '#000000'} emissiveIntensity={selected ? 0.28 : 0} />
      </mesh>
      {building.floors >= 4 && (
        <mesh position={[0, height + 0.09, 0]}>
          <boxGeometry args={[building.footprint[0] * 0.7, 0.14, building.footprint[1] * 0.7]} />
          <meshStandardMaterial color="#9ca39a" />
        </mesh>
      )}
    </group>
  )
}

function GridNodeMesh({ node, selected, onSelect }: { node: GridNode; selected: boolean; onSelect: () => void }) {
  const group = useRef<THREE.Group>(null)
  useFrame(({ clock }) => {
    if (group.current && (node.kind === 'generator' || selected)) group.current.rotation.y = clock.elapsedTime * 0.2
  })
  const colors = { generator: '#f6c453', 'main-station': '#ff7657', substation: '#51d4c6', transformer: '#74a7ff' }
  const scale = node.kind === 'generator' ? 1.5 : node.kind === 'main-station' ? 1.35 : node.kind === 'substation' ? 1.05 : 0.7
  return (
    <group ref={group} position={[node.x, 0.3, node.z]} onClick={(e) => { e.stopPropagation(); onSelect() }}>
      <mesh castShadow position={[0, scale, 0]}>
        {node.kind === 'generator' ? <cylinderGeometry args={[1.5, 2.1, 0.5, 8]} /> : <octahedronGeometry args={[scale, 0]} />}
        <meshStandardMaterial color={colors[node.kind]} emissive={colors[node.kind]} emissiveIntensity={selected ? 1.1 : 0.45} roughness={0.35} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.04, 0]}>
        <ringGeometry args={[scale * 1.3, scale * 1.65, 32]} />
        <meshBasicMaterial color={colors[node.kind]} transparent opacity={selected ? 0.95 : 0.3} />
      </mesh>
      {(node.kind !== 'transformer' || selected) && (
        <Html center position={[0, scale * 2.5, 0]} distanceFactor={13} style={{ pointerEvents: 'none' }}>
          <div className={`map-label ${selected ? 'selected' : ''}`}><span>{node.voltageKv} kV</span>{node.name}</div>
        </Html>
      )}
    </group>
  )
}

function FlowLine({ from, to, voltage, utilization, running }: { from: Point3; to: Point3; voltage: number; utilization: number; running: boolean }) {
  const lineRef = useRef<any>(null)
  useFrame((_, delta) => {
    if (running && lineRef.current?.material) lineRef.current.material.dashOffset -= delta * (0.6 + utilization)
  })
  const color = voltage >= 200 ? '#f6c453' : voltage >= 60 ? '#ff7657' : '#51d4c6'
  const peak = voltage >= 200 ? 5 : voltage >= 60 ? 3.3 : 2.1
  const points: Point3[] = [from, [(from[0] + to[0]) / 2, peak, (from[2] + to[2]) / 2], to]
  return <Line ref={lineRef} points={points} color={color} lineWidth={voltage >= 200 ? 2.2 : 1.35} dashed dashSize={0.75} gapSize={0.5} transparent opacity={0.45 + utilization * 0.45} />
}

type Point3 = [number, number, number]

function CameraKeyboard() {
  const { camera } = useThree()
  const held = useRef(new Set<string>())
  useEffect(() => {
    const down = (e: KeyboardEvent) => held.current.add(e.key.toLowerCase())
    const up = (e: KeyboardEvent) => held.current.delete(e.key.toLowerCase())
    window.addEventListener('keydown', down); window.addEventListener('keyup', up)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up) }
  }, [])
  useFrame((_, delta) => {
    const speed = delta * 15
    if (held.current.has('w') || held.current.has('arrowup')) camera.position.z -= speed
    if (held.current.has('s') || held.current.has('arrowdown')) camera.position.z += speed
    if (held.current.has('a') || held.current.has('arrowleft')) camera.position.x -= speed
    if (held.current.has('d') || held.current.has('arrowright')) camera.position.x += speed
  })
  return null
}

function CityScene({ selected, setSelected, running, layers }: { selected: Selectable | null; setSelected: (item: Selectable | null) => void; running: boolean; layers: Record<string, boolean> }) {
  const roads = [-32, -16, 0, 16, 32]
  return (
    <>
      <color attach="background" args={['#081311']} />
      <fog attach="fog" args={['#081311', 48, 105]} />
      <PerspectiveCamera makeDefault position={[45, 44, 49]} fov={43} />
      <OrbitControls makeDefault enableDamping dampingFactor={0.08} minDistance={16} maxDistance={100} maxPolarAngle={Math.PI / 2.15} screenSpacePanning />
      <CameraKeyboard />
      <ambientLight intensity={0.75} />
      <directionalLight position={[30, 45, 16]} intensity={2.2} color="#ffe7b0" castShadow shadow-mapSize={[2048, 2048]} />
      <pointLight position={[-30, 10, -20]} color="#f6c453" intensity={28} distance={34} />
      <group onPointerMissed={() => setSelected(null)}>
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[105, 90]} />
          <meshStandardMaterial color="#12201c" roughness={0.96} />
        </mesh>
        {roads.map((v) => <mesh key={`rv-${v}`} position={[v, 0.025, 0]} rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[1.65, 76]} /><meshStandardMaterial color="#29322e" /></mesh>)}
        {[-20, -4, 12, 28].map((v) => <mesh key={`rh-${v}`} position={[0, 0.027, v]} rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[78, 1.5]} /><meshStandardMaterial color="#29322e" /></mesh>)}
        {layers.buildings && buildings.map((building) => <BuildingMesh key={building.id} building={building} selected={selected?.id === building.id} onSelect={() => setSelected({ ...building, kind: 'building' })} />)}
        {layers.assets && gridNodes.map((node) => <GridNodeMesh key={node.id} node={node} selected={selected?.id === node.id} onSelect={() => setSelected(node)} />)}
        {layers.lines && powerLines.map((line) => {
          const a = nodeMap.get(line.from)!; const b = nodeMap.get(line.to)!
          return <FlowLine key={line.id} from={[a.x, 1.5, a.z]} to={[b.x, 1.5, b.z]} voltage={line.voltageKv} utilization={line.utilization} running={running} />
        })}
      </group>
      <gridHelper args={[100, 50, '#315046', '#20322c']} position={[0, 0.04, 0]} />
    </>
  )
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return <div className="metric"><span>{label}</span><strong className={accent ? 'accent' : ''}>{value}</strong></div>
}

function App() {
  const [selected, setSelected] = useState<Selectable | null>(null)
  const [running, setRunning] = useState(true)
  const [searchOpen, setSearchOpen] = useState(false)
  const [layersOpen, setLayersOpen] = useState(false)
  const [layers, setLayers] = useState({ buildings: true, lines: true, assets: true })
  const [time, setTime] = useState(new Date())
  useEffect(() => { const id = window.setInterval(() => setTime(new Date()), 1000); return () => window.clearInterval(id) }, [])
  const totalLoad = useMemo(() => gridNodes.filter(n => n.kind === 'substation').reduce((sum, n) => sum + n.capacityMw * n.load, 0), [])

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><div className="brand-mark"><Zap size={17} fill="currentColor" /></div><div><strong>UrjaSetu</strong><span>City Grid Live</span></div></div>
        <div className="status-line"><span className="live-dot" /> Grid synchronized <b>{citySummary.frequencyHz.toFixed(2)} Hz</b></div>
        <div className="top-actions">
          <button className="icon-button" aria-label="Search assets" onClick={() => { setSearchOpen(!searchOpen); setLayersOpen(false) }}><Search size={18} /></button>
          <button className="icon-button" aria-label="Toggle layers" onClick={() => { setLayersOpen(!layersOpen); setSearchOpen(false) }}><Layers3 size={18} /></button>
          <time>{time.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })} IST</time>
        </div>
      </header>

      <section className="viewport" aria-label="Interactive 3D power grid map">
        <Canvas shadows dpr={[1, 1.6]} gl={{ antialias: true }}>
          <CityScene selected={selected} setSelected={setSelected} running={running} layers={layers} />
        </Canvas>
        <div className="location-stamp"><span>{citySummary.state} / Live network model</span><h1>{citySummary.city}</h1><p>Drag to fly · scroll to change altitude</p></div>

        <aside className="left-rail">
          <div className="demand-block"><span>City demand</span><strong>{citySummary.totalDemandMw}<small> MW</small></strong><div className="trend"><Activity size={14} /> +4.8% since 18:00</div></div>
          <div className="rail-divider" />
          <Metric label="Grid draw" value={`${totalLoad.toFixed(1)} MW`} />
          <Metric label="Renewable" value={`${citySummary.renewableShare}%`} accent />
          <Metric label="Consumers" value={citySummary.consumers.toLocaleString('en-IN')} />
          <div className="load-strip"><div style={{ width: '79%' }} /><span>79% system load</span></div>
        </aside>

        <div className="legend">
          <span><i className="legend-line high" />220 kV</span><span><i className="legend-line medium" />66 kV</span><span><i className="legend-line low" />11 kV</span>
        </div>

        <div className="map-controls">
          <button onClick={() => setRunning(!running)}>{running ? <Pause size={16} /> : <Play size={16} />}{running ? 'Pause flow' : 'Resume flow'}</button>
          <button onClick={() => window.location.reload()} aria-label="Reset view"><RotateCcw size={16} /></button>
        </div>

        <div className="navigation-hint"><MousePointer2 size={16} /><span><b>Left drag</b> orbit &nbsp; <b>Right drag</b> pan &nbsp; <b>WASD</b> fly</span></div>

        {layersOpen && <div className="popover layers-popover"><h3>Map layers</h3>{Object.entries(layers).map(([key, value]) => <label key={key}><span>{key === 'assets' ? 'Grid assets' : key}</span><input type="checkbox" checked={value} onChange={() => setLayers(prev => ({ ...prev, [key]: !prev[key as keyof typeof prev] }))} /></label>)}</div>}
        {searchOpen && <div className="popover search-popover"><div className="search-field"><Search size={16} /><input autoFocus placeholder="Find station or transformer" onChange={(e) => { const q = e.target.value.toLowerCase(); const found = gridNodes.find(n => n.name.toLowerCase().includes(q) || n.id.toLowerCase().includes(q)); if (q && found) setSelected(found) }} /></div><p>Try “Madhya” or “TX-04”</p></div>}

        {selected && <aside className="detail-panel">
          <button className="close-button" onClick={() => setSelected(null)} aria-label="Close details"><X size={17} /></button>
          <div className={`asset-icon ${selected.kind}`}>
            {selected.kind === 'building' ? <Building2 /> : selected.kind === 'generator' ? <Sun /> : selected.kind === 'transformer' ? <Zap /> : <RadioTower />}
          </div>
          <span className="asset-id">{selected.id}</span>
          <h2>{selected.name}</h2>
          {'floors' in selected ? <>
            <div className="detail-grid"><Metric label="Floors" value={String(selected.floors)} /><Metric label="Demand" value={`${selected.demandKw} kW`} /><Metric label="Feeder" value={selected.feeder} /><Metric label="Coordinates" value={`${selected.x.toFixed(1)}, ${selected.z.toFixed(1)}`} /></div>
          </> : <>
            <div className="load-gauge"><CircleGauge size={18} /><div><span>Current utilization</span><strong>{Math.round(selected.load * 100)}%</strong></div></div>
            <div className="gauge-track"><i style={{ width: `${selected.load * 100}%` }} /></div>
            <div className="detail-grid"><Metric label="Voltage" value={`${selected.voltageKv} kV`} /><Metric label="Capacity" value={`${selected.capacityMw} MW`} /><Metric label="Type" value={selected.kind.replace('-', ' ')} /><Metric label="Status" value="Online" accent /></div>
          </>}
          {('load' in selected && selected.load > 0.88) && <div className="warning"><AlertTriangle size={16} /><span>Load is nearing operating threshold.</span></div>}
          <button className="inspect-button">Open asset history <ChevronRight size={16} /></button>
        </aside>}
      </section>

      <footer className="ticker"><div><Gauge size={15} /><span>Frequency</span><b>50.02 Hz</b></div><div><Zap size={15} /><span>Import</span><b>18.7 MW</b></div><div><Sun size={15} /><span>Solar yield</span><b>121.3 MW</b></div><div><RadioTower size={15} /><span>Healthy assets</span><b>11 / 11</b></div><p>Simulation dataset · Updated just now</p></footer>
    </main>
  )
}

export default App
