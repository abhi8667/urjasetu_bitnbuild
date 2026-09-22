/** The city used to float in a fixed twilight void. This presentation-only
 * environment follows the simulation clock, with enough fill light at night
 * to inspect the grid. It never changes weather or energy calculations. */
import { useEffect, useMemo, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'

export type LightingMode = 'simulation' | 'sunrise' | 'day' | 'sunset' | 'night'

export function lightingAt(clock: string | undefined, mode: LightingMode) {
  const [h, m] = (clock ?? '12:00').split(':').map(Number)
  const hour = mode === 'day' ? 12 : mode === 'night' ? 0 : mode === 'sunrise' ? 6.3 : mode === 'sunset' ? 17.7 : Number.isFinite(h + m) ? h + m / 60 : 12
  const angle = (hour - 6) / 24 * Math.PI * 2
  const elevation = Math.sin(angle)
  const daylight = THREE.MathUtils.smoothstep(elevation, -.12, .35)
  const twilight = (1 - THREE.MathUtils.smoothstep(Math.abs(elevation), .02, .42))
  return { hour, daylight, twilight, night: 1 - THREE.MathUtils.smoothstep(elevation, -.08, .18),
    sun: new THREE.Vector3(Math.cos(angle) * 90, elevation * 90, -45) }
}

export type CityLighting = ReturnType<typeof lightingAt>

export function CityEnvironment({ lighting, extent }: { lighting: CityLighting; extent: number }) {
  const { scene } = useThree()
  const sunLight = useRef<THREE.DirectionalLight>(null)
  const fill = useRef<THREE.HemisphereLight>(null)
  const moon = useRef<THREE.DirectionalLight>(null)
  const sun = useRef<THREE.Mesh>(null)
  const moonDisc = useRef<THREE.Mesh>(null)
  const sky = useMemo(() => new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false,
    uniforms: { zenith: { value: new THREE.Color('#25465c') }, horizon: { value: new THREE.Color('#b6c8ce') }, daylight: { value: lighting.daylight } },
    vertexShader: 'varying vec3 direction; void main(){ direction=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
    fragmentShader: `
      varying vec3 direction;
      uniform vec3 zenith;
      uniform vec3 horizon;
      uniform float daylight;
      float hash(vec2 p) { return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453); }
      float noise(vec2 p) {
        vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f);
        return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);
      }
      void main() {
        vec3 d=normalize(direction);
        float h=smoothstep(-0.04,0.7,d.y);
        vec3 color=mix(horizon,zenith,h);
        vec2 p=d.xz/max(.08,d.y)*2.5;
        float cloud=noise(p)*.55+noise(p*2.1)*.3+noise(p*4.3)*.15;
        float cover=smoothstep(.51,.76,cloud)*smoothstep(.025,.2,d.y)*.55;
        color=mix(color,mix(vec3(.04,.07,.1),vec3(.94,.94,.9),daylight),cover);
        gl_FragColor=vec4(color,1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  }), [])
  const sunPosition = useMemo(() => lighting.sun.clone().multiplyScalar(2.2), [lighting])
  const target = useMemo(() => {
    const zenith = new THREE.Color('#152941').lerp(new THREE.Color('#578baa'), lighting.daylight)
    const horizon = new THREE.Color('#344858').lerp(new THREE.Color('#c7d8d9'), lighting.daylight)
      .lerp(new THREE.Color('#e7a579'), lighting.twilight * .8)
    return { zenith, horizon, sunColor: new THREE.Color('#fff4dd').lerp(new THREE.Color('#ffb575'), lighting.twilight) }
  }, [lighting.daylight, lighting.twilight])
  useEffect(() => {
    const previous = scene.fog
    // Fog begins beyond the active network, never over the inspected houses.
    scene.fog = new THREE.Fog(target.horizon, extent * 1.9, extent * 5.5)
    return () => { scene.fog = previous }
  }, [scene, extent])
  useEffect(() => () => sky.dispose(), [sky])
  useFrame((_, dt) => {
    const blend = 1 - Math.exp(-Math.min(dt, .1) * 2.5)
    sky.uniforms.zenith.value.lerp(target.zenith, blend)
    sky.uniforms.horizon.value.lerp(target.horizon, blend)
    sky.uniforms.daylight.value = THREE.MathUtils.lerp(sky.uniforms.daylight.value, lighting.daylight, blend)
    if (scene.fog instanceof THREE.Fog) scene.fog.color.lerp(target.horizon, blend)
    if (fill.current) fill.current.intensity = THREE.MathUtils.lerp(fill.current.intensity, 1.25 + lighting.daylight * 1.05, blend)
    if (sunLight.current) {
      sunLight.current.position.lerp(lighting.sun, blend)
      sunLight.current.color.lerp(target.sunColor, blend)
      sunLight.current.intensity = THREE.MathUtils.lerp(sunLight.current.intensity, lighting.daylight * 3.2, blend)
    }
    if (moon.current) moon.current.intensity = THREE.MathUtils.lerp(moon.current.intensity, .35 + lighting.night * 1.1, blend)
    if (sun.current) { sun.current.position.lerp(sunPosition, blend); sun.current.visible = lighting.sun.y > -12 }
    if (moonDisc.current) moonDisc.current.visible = lighting.daylight < .5
  })
  return <>
    <mesh material={sky} frustumCulled={false}><sphereGeometry args={[450, 32, 16]} /></mesh>
    <mesh ref={sun} position={sunPosition}>
      <sphereGeometry args={[5, 24, 16]} /><meshBasicMaterial color="#fff1c4" fog={false} toneMapped={false} />
    </mesh>
    <mesh ref={moonDisc} position={[-100, 120, -160]}>
      <sphereGeometry args={[3, 20, 12]} /><meshBasicMaterial color="#dce7f1" fog={false} />
    </mesh>
    <hemisphereLight ref={fill} args={['#c4dbef', '#617062', 1.5]} />
    <ambientLight intensity={.28} color="#b8c9d8" />
    <directionalLight ref={sunLight} position={lighting.sun} color="#fff4dd" intensity={2} castShadow
      shadow-mapSize={[2048, 2048]} shadow-camera-left={-extent} shadow-camera-right={extent}
      shadow-camera-top={extent} shadow-camera-bottom={-extent} shadow-camera-far={300}
      shadow-normalBias={.035} shadow-bias={-.00015} />
    <directionalLight ref={moon} position={[-40, 55, 30]} intensity={1} color="#b8d0f3" />
  </>
}

type SceneryPart = { position: [number, number, number]; scale: [number, number, number]; color: string }

function SceneryBatch({ parts, shape = 'box', night = 0, windows = false }: {
  parts: SceneryPart[]; shape?: 'box' | 'tree' | 'hill'; night?: number; windows?: boolean
}) {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const material = useRef<THREE.MeshStandardMaterial>(null)
  useEffect(() => {
    if (!mesh.current) return
    const dummy = new THREE.Object3D()
    parts.forEach((part, index) => {
      dummy.position.set(...part.position); dummy.scale.set(...part.scale); dummy.updateMatrix()
      mesh.current!.setMatrixAt(index, dummy.matrix)
      mesh.current!.setColorAt(index, new THREE.Color(part.color))
    })
    mesh.current.instanceMatrix.needsUpdate = true
    if (mesh.current.instanceColor) mesh.current.instanceColor.needsUpdate = true
    mesh.current.computeBoundingSphere()
  }, [parts])
  useFrame((_, dt) => {
    if (material.current && windows) material.current.emissiveIntensity = THREE.MathUtils.damp(material.current.emissiveIntensity, night * 1.4, 2.5, Math.min(dt, .1))
  })
  return <instancedMesh ref={mesh} args={[undefined, undefined, parts.length]} castShadow={!windows} receiveShadow>
    {shape === 'box' ? <boxGeometry /> : shape === 'hill' ? <sphereGeometry args={[1, 24, 12]} /> : <icosahedronGeometry args={[1, 2]} />}
    <meshStandardMaterial ref={material} roughness={windows ? .3 : .94} emissive={windows ? '#efb96f' : '#000000'} emissiveIntensity={0} />
  </instancedMesh>
}

/** Deterministic, instanced scenery is decorative: no invented meters or
 * simulated loads. Keep it outside the actual neighborhood's bounds. */
export function CitySurroundings({ width, depth, night }: { width: number; depth: number; night: number }) {
  const parts = useMemo(() => {
    const buildings: SceneryPart[] = [], windows: SceneryPart[] = [], trees: SceneryPart[] = [], trunks: SceneryPart[] = [], roads: SceneryPart[] = []
    const add = (list: SceneryPart[], position: SceneryPart['position'], scale: SceneryPart['scale'], color: string) => list.push({ position, scale, color })
    const radius = Math.max(width, depth) / 2
    // A perimeter avenue, pavements, and roads connecting the neighborhood.
    for (const side of [-1, 1]) {
      add(roads, [0, -.075, side * (depth / 2 + 5)], [width + 15, .08, 3], '#626b6b')
      add(roads, [side * (width / 2 + 5), -.075, 0], [3, .08, depth + 13], '#626b6b')
      add(roads, [0, -.08, side * (depth / 2 + 7)], [width + 15, .1, .8], '#a3a89d')
      for (let x = -width / 2; x < width / 2; x += 3) add(roads, [x, -.02, side * (depth / 2 + 5)], [1.1, .02, .08], '#d8d5be')
    }
    for (let i = 0; i < 36; i++) {
      const a = i / 36 * Math.PI * 2
      const r = radius + 15 + (i % 3) * 7
      const x = Math.cos(a) * r, z = Math.sin(a) * r
      const h = 2.4 + (i * 7 % 9) * .7
      add(buildings, [x, h / 2 - .1, z], [2.6, h, 3.2], ['#929e9c', '#b2b1a5', '#8c9ca5', '#c0b7a6'][i % 4])
      add(buildings, [x, h, z], [2.8, .16, 3.4], '#657578')
      add(buildings, [x + .5, h + .25, z], [.8, .4, .9], '#7d8686')
      for (let floor = .8; floor < h - .3; floor += .95) for (const dx of [-.8, 0, .8]) {
        if ((i + Math.round(floor * 10) + Math.round(dx * 10)) % 4 === 0) continue
        add(windows, [x + dx, floor, z + 1.61], [.34, .43, .025], '#a9b4ad')
        add(windows, [x + 1.31, floor, z + dx], [.025, .43, .34], '#a9b4ad')
      }
    }
    for (let i = 0; i < 100; i++) {
      const a = i * 2.39996, r = radius + 9 + (i * 13 % 35)
      const x = Math.cos(a) * r, z = Math.sin(a) * r, s = .85 + (i % 5) * .18
      add(trunks, [x, .7 * s, z], [.2, 1.5 * s, .2], '#66533d')
      add(trees, [x, 2 * s, z], [.95 * s, 1.3 * s, .95 * s], ['#3f6550', '#527559', '#607951'][i % 3])
    }
    return { buildings, windows, trees, trunks, roads }
  }, [width, depth])
  const terrain = useMemo(() => {
    const geometry = new THREE.PlaneGeometry(900, 900, 120, 120)
    geometry.rotateX(-Math.PI / 2)
    const positions = geometry.attributes.position
    const radius = Math.max(width, depth) / 2
    for (let i = 0; i < positions.count; i++) {
      const x = positions.getX(i), z = positions.getZ(i)
      const rise = THREE.MathUtils.smoothstep(Math.hypot(x, z), radius + 48, radius + 125)
      const rolling = 18 + Math.sin(x * .022 + z * .016) * 10 + Math.sin(z * .043 - x * .013) * 7
        + Math.sin(x * .069 + z * .052) * 3
      positions.setY(i, -.16 + rise * rolling)
    }
    geometry.computeVertexNormals()
    return geometry
  }, [width, depth])
  useEffect(() => () => terrain.dispose(), [terrain])
  return <group>
    <mesh geometry={terrain} receiveShadow>
      <meshStandardMaterial color="#657463" roughness={1} />
    </mesh>
    <SceneryBatch parts={parts.roads} />
    <SceneryBatch parts={parts.buildings} />
    <SceneryBatch parts={parts.windows} windows night={night} />
    <SceneryBatch parts={parts.trunks} />
    <SceneryBatch parts={parts.trees} shape="tree" />
  </group>
}
