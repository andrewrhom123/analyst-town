import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { freshness } from "../format.js";
import Robot from "./Robot.jsx";

/**
 * One agent's house. Color-coded; window glow = how recently the agent's thinking was updated
 * (brighter = fresher); windows flicker while the agent is working. Click to enter the office.
 */
export default function House({ agent, position, rotation = 0, onEnter, index = 0, hall = false }) {
  const group = useRef();
  const [hovered, setHovered] = useState(false);
  const color = agent?.color || "#a9b6cc";
  // One shared material so every window glows/flickers together.
  const windowMat = useMemo(() => new THREE.MeshStandardMaterial({ color, emissive: color }), [color]);
  const glow = hall ? 0.7 : freshness(agent?.last_update);
  const working = agent?.status === "working";
  const scaleTarget = hovered ? 1.06 : 1;

  const windows = useMemo(() => {
    const out = [];
    for (let row = 0; row < 2; row++) for (let col = 0; col < 3; col++) out.push([(col - 1) * 0.62, 0.75 + row * 0.75]);
    return out;
  }, []);

  useFrame(({ clock }, delta) => {
    if (group.current) {
      const s = THREE.MathUtils.damp(group.current.scale.x, scaleTarget, 10, delta);
      group.current.scale.setScalar(s);
    }
    const flicker = working ? Math.sin(clock.elapsedTime * 7 + index) * 0.35 : 0;
    windowMat.emissiveIntensity = 0.55 + glow * 1.6 + flicker + (hovered ? 0.5 : 0);
  });

  const width = hall ? 3.4 : 2.4;
  const height = hall ? 2.2 : 1.9;

  return (
    <group position={position} rotation={[0, rotation, 0]}>
      <group
        ref={group}
        onClick={(e) => { e.stopPropagation(); onEnter?.(); }}
        onPointerOver={(e) => { e.stopPropagation(); setHovered(true); document.body.style.cursor = "pointer"; }}
        onPointerOut={() => { setHovered(false); document.body.style.cursor = ""; }}
      >
        {/* foundation glow ring */}
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.01, 0]}>
          <ringGeometry args={[width * 0.78, width * 0.84, 48]} />
          <meshBasicMaterial color={color} transparent opacity={hovered ? 0.85 : 0.45} />
        </mesh>
        {/* body */}
        <mesh position={[0, height / 2, 0]} castShadow receiveShadow>
          <boxGeometry args={[width, height, 2]} />
          <meshStandardMaterial color="#26345c" metalness={0.25} roughness={0.55} emissive={color} emissiveIntensity={0.06} />
        </mesh>
        {/* neon trim */}
        <mesh position={[0, height + 0.02, 0]}>
          <boxGeometry args={[width + 0.08, 0.06, 2.08]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.6} />
        </mesh>
        {/* roof */}
        <mesh position={[0, height + 0.62, 0]} rotation={[0, Math.PI / 4, 0]} castShadow>
          <coneGeometry args={[hall ? 2.6 : 1.85, 1.2, 4]} />
          <meshStandardMaterial color="#334373" metalness={0.35} roughness={0.5} emissive={color} emissiveIntensity={0.08} />
        </mesh>
        {/* windows (front) */}
        {windows.map(([x, y], i) => (
          <group key={i}>
            <mesh position={[x * (hall ? 1.3 : 1), y * (height / 1.9), 1.005]} material={windowMat}>
              <planeGeometry args={[0.36, 0.42]} />
            </mesh>
            {/* back windows, so the house reads from every side of the orbit */}
            <mesh position={[x * (hall ? 1.3 : 1), y * (height / 1.9), -1.005]} rotation={[0, Math.PI, 0]} material={windowMat}>
              <planeGeometry args={[0.36, 0.42]} />
            </mesh>
          </group>
        ))}
        {/* door */}
        <mesh position={[0, 0.42, 1.006]}>
          <planeGeometry args={[0.46, 0.84]} />
          <meshStandardMaterial color="#0a0f1e" emissive={color} emissiveIntensity={hovered ? 0.6 : 0.15} />
        </mesh>
        {!hall && <Robot color={color} working={working} phase={index * 1.3} position={[width / 2 + 0.55, 0, 0.9]} rotation={[0, -0.4, 0]} />}
        <Html position={[0, height + 1.55, 0]} center zIndexRange={[10, 0]}>
          <div className="house-label">
            <span className="name" style={{ borderColor: color, color }}>{hall ? "Town Hall" : agent.name.replace(" Analyst", "")}</span>
            <div className="meta">
              {hall ? "Daily meeting · click to open" : (
                <>
                  <b>{agent.ticker_count}</b> tickers · conviction <b>{agent.avg_conviction ?? "–"}</b> · {agent.status}
                </>
              )}
            </div>
          </div>
        </Html>
      </group>
    </group>
  );
}
