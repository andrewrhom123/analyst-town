import { ContactShadows, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useMemo } from "react";
import * as THREE from "three";
import { Label, LabelLayer } from "./Label.jsx";
import CentralPark, { PARK } from "./Park.jsx";
import { Chair, CoffeeCup, LiveMonitor, Papers, Plant } from "./Props.jsx";
import Robot from "./Robot.jsx";
import { seedOf } from "./screens.js";

const FLOOR = 7; // boardroom height above the park: low enough that the lawn and lake fill the window
const ROOM = { w: 9, d: 9, h: 3.2 };
const TABLE = { w: 1.8, l: 5.2 };
const CHAIR_COLOR = "#a855f7";
const SOUTH = ["south"]; // the tower's own row of buildings would block the view

/** Seats alternate left/right down the table; the chair sits at the head by the window. */
function seats(n) {
  const perSide = Math.max(1, Math.ceil(n / 2));
  return Array.from({ length: n }, (_, i) => {
    const side = i % 2 === 0 ? -1 : 1;
    const row = Math.floor(i / 2);
    const z = -TABLE.l / 2 + 1.1 + (row + 0.5) * ((TABLE.l - 1.6) / perSide);
    return { position: [side * (TABLE.w / 2 + 0.55), 0, z], rotation: [0, side * Math.PI / 2, 0] };
  });
}

function Seat({ color, name, position, rotation, talking, phase }) {
  return (
    <group position={position} rotation={rotation}>
      <Chair position={[0, 0, 0.1]} rotation={[0, Math.PI, 0]} scale={1.1} color="#374151" />
      <Robot color={color} talking={talking} seated phase={phase} position={[0, 0.26, 0.05]} rotation={[0, Math.PI, 0]} scale={0.62} />
      {/* spotlight ring on the floor under whoever is speaking */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.012, 0]}>
        <ringGeometry args={[0.45, 0.55, 48]} />
        <meshBasicMaterial color={color} transparent opacity={talking ? 0.95 : 0.0} />
      </mesh>
      <Label position={[0, 1.55, 0]} center zIndexRange={[10, 0]}>
        <div className={`seat-label ${talking ? "on" : ""}`} style={{ "--c": color }}>{name}</div>
      </Label>
    </group>
  );
}

/** Glass curtain wall with mullions, looking north over the park. */
function GlassWall() {
  return (
    <group position={[0, 0, -ROOM.d / 2]}>
      <mesh position={[0, ROOM.h / 2, 0]}>
        <planeGeometry args={[ROOM.w, ROOM.h]} />
        <meshPhysicalMaterial color="#e0f2fe" transparent opacity={0.08} roughness={0.02} metalness={0.1} depthWrite={false} />
      </mesh>
      {[-3, -1.5, 0, 1.5, 3].map((x) => (
        <mesh key={x} position={[x, ROOM.h / 2, 0.02]}><boxGeometry args={[0.05, ROOM.h, 0.06]} /><meshStandardMaterial color="#e5e7eb" metalness={0.5} /></mesh>
      ))}
      <mesh position={[0, 0.05, 0.02]}><boxGeometry args={[ROOM.w, 0.1, 0.08]} /><meshStandardMaterial color="#f3f4f6" /></mesh>
    </group>
  );
}

/**
 * The boardroom atop the tower on Central Park South. The pod sits around the table; whoever is speaking
 * (agent key, or "chair") lights up and animates. Outside the glass: the park, the lake and the skyline.
 */
export default function BoardroomScene({ agents, speaker }) {
  const places = useMemo(() => seats(agents.length), [agents.length]);
  const clearings = useMemo(() => [{ x: 0, z: 0, r: 13 }], []);

  return (
    <LabelLayer>
      <Canvas shadows dpr={[1, 2]} camera={{ position: [0, 2.6, 5.4], fov: 50, far: 600 }} gl={{ antialias: true, toneMapping: THREE.NeutralToneMapping }}>
        <color attach="background" args={["#dbeefb"]} />
        <fog attach="fog" args={["#dbeefb", 90, 260]} />
        <ambientLight intensity={1.1} />
        <hemisphereLight args={["#ffffff", "#e7e1d8", 1.0]} />
        <pointLight position={[0, ROOM.h - 0.3, 0.5]} intensity={6} distance={9} color="#ffffff" />
        <directionalLight position={[6, 14, -10]} intensity={2} castShadow shadow-mapSize={[1024, 1024]}
          shadow-camera-left={-6} shadow-camera-right={6} shadow-camera-top={6} shadow-camera-bottom={-6} />

        {/* the park far below: rotated so its south edge (where the tower stands, as in the town) is under us */}
        <group position={[0, -FLOOR, -(PARK.halfZ + 5)]} rotation={[0, Math.PI, 0]}>
          <CentralPark clearings={clearings} trees={320} omitSkyline={SOUTH} />
          {/* the town plaza and hall, small in the distance */}
          <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.01, 0]}><circleGeometry args={[11.6, 64]} /><meshStandardMaterial color="#ffffff" /></mesh>
          <mesh position={[0, 1.5, 0]}><cylinderGeometry args={[2.3, 2.3, 3, 24]} /><meshStandardMaterial color="#f8fafc" /></mesh>
          <mesh position={[0, 3.2, 0]}><sphereGeometry args={[1, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2]} /><meshStandardMaterial color="#f8fafc" /></mesh>
          {agents.map((a, i) => {
            const ang = (i / agents.length) * Math.PI * 2 + Math.PI / 4;
            return (
              <mesh key={a.id} position={[Math.sin(ang) * 8, 1.1, Math.cos(ang) * 8]}>
                <boxGeometry args={[2.8, 2.2, 2.4]} />
                <meshStandardMaterial color="#ffffff" emissive={a.color} emissiveIntensity={speaker === a.key ? 0.6 : 0.08} />
              </mesh>
            );
          })}
        </group>

        {/* room shell: pale oak floor, white walls, ceiling light strips */}
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow><planeGeometry args={[ROOM.w, ROOM.d]} /><meshStandardMaterial color="#e9dfd1" roughness={0.75} /></mesh>
        <mesh position={[0, ROOM.h, 0]} rotation={[Math.PI / 2, 0, 0]}><planeGeometry args={[ROOM.w, ROOM.d]} /><meshStandardMaterial color="#ffffff" /></mesh>
        {[-1.2, 1.2].map((x) => (
          <mesh key={x} position={[x, ROOM.h - 0.02, -0.5]} rotation={[Math.PI / 2, 0, 0]}><planeGeometry args={[0.18, 6]} /><meshBasicMaterial color="#ffffff" /></mesh>
        ))}
        {[-1, 1].map((s) => (
          <mesh key={s} position={[s * ROOM.w / 2, ROOM.h / 2, 0]} rotation={[0, -s * Math.PI / 2, 0]} receiveShadow>
            <planeGeometry args={[ROOM.d, ROOM.h]} /><meshStandardMaterial color="#ffffff" roughness={0.9} />
          </mesh>
        ))}
        <mesh position={[0, ROOM.h / 2, ROOM.d / 2]} rotation={[0, Math.PI, 0]}><planeGeometry args={[ROOM.w, ROOM.h]} /><meshStandardMaterial color="#ffffff" /></mesh>
        <mesh position={[0, 0.04, -ROOM.d / 2 + 0.02]}><boxGeometry args={[ROOM.w, 0.08, 0.04]} /><meshStandardMaterial color="#d946ef" emissive="#d946ef" emissiveIntensity={0.8} toneMapped={false} /></mesh>
        <GlassWall />

        {/* side-wall market screens */}
        <LiveMonitor position={[-ROOM.w / 2 + 0.06, 1.75, -0.6]} rotation={[0, Math.PI / 2, 0]} width={2.2} height={1.2} color="#06b6d4" seed={201} label="POD BOARD" />
        <LiveMonitor position={[ROOM.w / 2 - 0.06, 1.75, -0.6]} rotation={[0, -Math.PI / 2, 0]} width={2.2} height={1.2} color="#d946ef" seed={202} label="CROSS-TICKER" variant={1} />

        {/* the table: white top, neon edge, laptops and papers at each seat */}
        <group position={[0, 0, -0.6]}>
          <mesh position={[0, 0.74, 0]} castShadow receiveShadow><boxGeometry args={[TABLE.w, 0.06, TABLE.l]} /><meshStandardMaterial color="#ffffff" roughness={0.3} /></mesh>
          <mesh position={[0, 0.705, 0]}><boxGeometry args={[TABLE.w + 0.02, 0.012, TABLE.l + 0.02]} /><meshStandardMaterial color="#d946ef" emissive="#d946ef" emissiveIntensity={0.9} toneMapped={false} /></mesh>
          {[-1.6, 1.6].map((z) => (
            <mesh key={z} position={[0, 0.36, z]} castShadow><boxGeometry args={[0.5, 0.72, 0.5]} /><meshStandardMaterial color="#e5e7eb" metalness={0.4} /></mesh>
          ))}
          {places.map((p, i) => (
            <group key={i}>
              <Papers position={[p.position[0] * 0.6, 0.775, p.position[2]]} seed={seedOf(agents[i]?.id || String(i))} count={3} scale={1.3} />
              <CoffeeCup position={[p.position[0] * 0.42, 0.775, p.position[2] + 0.3]} color={agents[i]?.color} scale={1.2} />
            </group>
          ))}
          {places.map((p, i) => (
            <Seat key={agents[i].id} {...p} color={agents[i].color} name={agents[i].name.replace(/ (Analyst|Strategist)$/, "")}
              talking={speaker === agents[i].key} phase={i * 1.1} />
          ))}
          {/* the chair at the head of the table, back to the window */}
          <Seat position={[0, 0, -TABLE.l / 2 - 0.55]} rotation={[0, Math.PI, 0]} color={CHAIR_COLOR} name="Chair" talking={speaker === "chair"} phase={9} />
        </group>
        <Plant position={[-ROOM.w / 2 + 0.6, 0, -ROOM.d / 2 + 0.6]} scale={3} />
        <Plant position={[ROOM.w / 2 - 0.6, 0, -ROOM.d / 2 + 0.6]} scale={3} />
        <ContactShadows position={[0, 0.005, 0]} opacity={0.25} scale={10} blur={2.2} far={3} />

        <OrbitControls enablePan={false} minDistance={3} maxDistance={7.5} minPolarAngle={0.9} maxPolarAngle={1.5}
          minAzimuthAngle={-0.7} maxAzimuthAngle={0.7} target={[0, 1.1, -1.2]} />
      </Canvas>
    </LabelLayer>
  );
}
