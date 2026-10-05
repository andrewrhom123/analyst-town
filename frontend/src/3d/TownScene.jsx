import { ContactShadows, OrbitControls, Sky } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import * as THREE from "three";
import { useRef, useState } from "react";
import { CameraRig } from "./animations.js";
import House, { TownHall } from "./House.jsx";

const RADIUS = 8;
const PATH_MID = 4.85 / RADIUS; // paths run from the hall's plinth to the back of each house

/** Light plaza: white ground, pale paths radiating to each house, a thin neon ring around the hall. */
function Ground({ placements }) {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.02, 0]} receiveShadow>
        <circleGeometry args={[60, 64]} />
        <meshStandardMaterial color="#eef2f5" roughness={1} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, 0]} receiveShadow>
        <circleGeometry args={[RADIUS + 3.6, 96]} />
        <meshStandardMaterial color="#ffffff" roughness={0.95} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.005, 0]}>
        <ringGeometry args={[3.3, 3.36, 96]} />
        <meshBasicMaterial color="#06b6d4" transparent opacity={0.55} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.006, 0]}>
        <ringGeometry args={[RADIUS - 2.4, RADIUS - 1.9, 96]} />
        <meshStandardMaterial color="#e5e9ee" roughness={1} />
      </mesh>
      {placements.map((p) => (
        <mesh key={p.agent.id} rotation={[-Math.PI / 2, 0, p.rotation]} position={[p.position[0] * PATH_MID, -0.004, p.position[2] * PATH_MID]}>
          <planeGeometry args={[0.9, 3.1]} />
          <meshStandardMaterial color="#e5e9ee" roughness={1} />
        </mesh>
      ))}
    </group>
  );
}

/** 3D town: one glass-front studio per agent in a ring around the town hall (meetings). */
export default function TownScene({ agents, onEnterAgent, onOpenHall }) {
  const controls = useRef();
  const [flyTo, setFlyTo] = useState(null);
  const [pending, setPending] = useState(null);

  const placements = agents.map((agent, i) => {
    const angle = (i / Math.max(agents.length, 1)) * Math.PI * 2 + Math.PI / 4;
    return { agent, position: [Math.sin(angle) * RADIUS, 0, Math.cos(angle) * RADIUS], rotation: angle }; // glass fronts face outward
  });

  const enter = (p) => {
    const [x, , z] = p.position;
    const dir = Math.hypot(x, z) || 1;
    setPending(p.agent);
    setFlyTo({ position: [x + (x / dir) * 3.4, 1.5, z + (z / dir) * 3.4], lookAt: [x, 0.9, z] });
  };

  return (
    <Canvas
      className="town-canvas"
      shadows
      dpr={[1, 2]}
      camera={{ position: [0, 10, 19], fov: 42 }}
      gl={{ antialias: true, powerPreference: "high-performance", toneMapping: THREE.NeutralToneMapping }}
    >
      <color attach="background" args={["#eaf4fb"]} />
      <fog attach="fog" args={["#eaf4fb", 40, 80]} />
      <Sky distance={450} sunPosition={[10, 18, 8]} turbidity={2} rayleigh={0.4} mieCoefficient={0.003} />
      <ambientLight intensity={0.5} />
      <hemisphereLight args={["#ffffff", "#dfe5ec", 0.8]} />
      <directionalLight
        position={[10, 18, 8]}
        intensity={2.4}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-16}
        shadow-camera-right={16}
        shadow-camera-top={16}
        shadow-camera-bottom={-16}
        shadow-bias={-0.0004}
      />
      <Ground placements={placements} />
      <ContactShadows position={[0, 0, 0]} opacity={0.25} scale={40} blur={2.4} far={6} resolution={1024} />

      <TownHall onEnter={onOpenHall} />
      {placements.map((p, i) => (
        <House key={p.agent.id} agent={p.agent} index={i} position={p.position} rotation={p.rotation} onEnter={() => enter(p)} />
      ))}

      <OrbitControls
        ref={controls}
        enablePan={false}
        minDistance={9}
        maxDistance={28}
        minPolarAngle={0.5}
        maxPolarAngle={1.3}
        autoRotate={!flyTo}
        autoRotateSpeed={0.3}
        target={[0, 1, 0]}
      />
      <CameraRig flyTo={flyTo} controlsRef={controls} onArrive={() => pending && onEnterAgent(pending)} />
    </Canvas>
  );
}
