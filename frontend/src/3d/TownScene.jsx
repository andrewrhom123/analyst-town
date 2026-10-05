import { Grid, OrbitControls, Stars } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useRef, useState } from "react";
import { CameraRig } from "./animations.js";
import House from "./House.jsx";

const RADIUS = 7.2;

/** 3D town: one house per agent in a ring around the town hall (meetings). */
export default function TownScene({ agents, onEnterAgent, onOpenHall }) {
  const controls = useRef();
  const [flyTo, setFlyTo] = useState(null);
  const [pending, setPending] = useState(null);

  const placements = agents.map((agent, i) => {
    const angle = (i / Math.max(agents.length, 1)) * Math.PI * 2 + Math.PI / 4;
    const x = Math.sin(angle) * RADIUS;
    const z = Math.cos(angle) * RADIUS;
    return { agent, position: [x, 0, z], rotation: angle }; // houses face outward toward the camera ring
  });

  const enter = (p) => {
    const [x, , z] = p.position;
    const dir = Math.hypot(x, z) || 1;
    setPending(p.agent);
    setFlyTo({ position: [x + (x / dir) * 3.2, 1.6, z + (z / dir) * 3.2], lookAt: [x, 1.1, z] });
  };

  return (
    <Canvas
      className="town-canvas"
      shadows
      dpr={[1, 2]}
      camera={{ position: [0, 9.5, 17], fov: 45 }}
      gl={{ antialias: true, powerPreference: "high-performance" }}
    >
      <color attach="background" args={["#070b16"]} />
      <fog attach="fog" args={["#070b16", 22, 48]} />
      <ambientLight intensity={0.6} />
      <hemisphereLight args={["#7dd3fc", "#0b1224", 0.5]} />
      <directionalLight position={[8, 14, 6]} intensity={1.1} castShadow shadow-mapSize={[1024, 1024]} />
      <pointLight position={[0, 6, 0]} intensity={18} color="#e879f9" distance={22} />
      <Stars radius={60} depth={30} count={1400} factor={3} fade speed={0.4} />
      <Grid
        args={[60, 60]}
        position={[0, 0, 0]}
        cellSize={1}
        cellThickness={0.5}
        cellColor="#16223f"
        sectionSize={5}
        sectionThickness={1}
        sectionColor="#1f6f8b"
        fadeDistance={42}
        fadeStrength={1.5}
        infiniteGrid
      />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, 0]} receiveShadow>
        <circleGeometry args={[RADIUS + 4, 64]} />
        <meshStandardMaterial color="#0b1224" roughness={0.9} />
      </mesh>

      <House hall position={[0, 0, 0]} rotation={0} onEnter={onOpenHall} />
      {placements.map((p, i) => (
        <House key={p.agent.id} agent={p.agent} index={i} position={p.position} rotation={p.rotation} onEnter={() => enter(p)} />
      ))}

      <OrbitControls
        ref={controls}
        enablePan={false}
        minDistance={9}
        maxDistance={26}
        minPolarAngle={0.5}
        maxPolarAngle={1.32}
        autoRotate={!flyTo}
        autoRotateSpeed={0.35}
        target={[0, 1, 0]}
      />
      <CameraRig flyTo={flyTo} controlsRef={controls} onArrive={() => pending && onEnterAgent(pending)} />
    </Canvas>
  );
}
