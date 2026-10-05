import { useFrame } from "@react-three/fiber";
import { useRef, useState } from "react";
import * as THREE from "three";
import { freshness } from "../format.js";
import { Label } from "./Label.jsx";
import { Chair, CoffeeCup, Desk, Keyboard, LiveMonitor, Papers, Plant } from "./Props.jsx";
import Robot from "./Robot.jsx";
import { seedOf } from "./screens.js";

const W = 2.8; // width
const D = 2.4; // depth
const H = 2.0; // wall height
const BASE = 0.12; // plinth height
const WALL = { color: "#ffffff", roughness: 0.6, metalness: 0.02 };
const GLASS = { color: "#e0f2fe", transparent: true, opacity: 0.16, roughness: 0.05, metalness: 0.1, depthWrite: false };

function useHover(scaleOn = 1.05) {
  const group = useRef();
  const [hovered, setHovered] = useState(false);
  useFrame((_, delta) => {
    if (!group.current) return;
    group.current.scale.setScalar(THREE.MathUtils.damp(group.current.scale.x, hovered ? scaleOn : 1, 10, delta));
  });
  const handlers = {
    onPointerOver: (e) => { e.stopPropagation(); setHovered(true); document.body.style.cursor = "pointer"; },
    onPointerOut: () => { setHovered(false); document.body.style.cursor = ""; },
  };
  return { group, hovered, handlers };
}

function Tree({ seed = 1, ...props }) {
  const h = 0.9 + (seed % 3) * 0.2;
  return (
    <group {...props}>
      <mesh position={[0, h / 2, 0]} castShadow><cylinderGeometry args={[0.05, 0.07, h, 8]} /><meshStandardMaterial color="#a8a29e" /></mesh>
      <mesh position={[0, h + 0.25, 0]} castShadow><icosahedronGeometry args={[0.42, 0]} /><meshStandardMaterial color="#bef264" roughness={0.9} flatShading /></mesh>
    </group>
  );
}

/**
 * One agent's house: a white modern studio with a floor-to-ceiling glass front, so you can watch the
 * robot typing at its desk while live charts tick on its monitors and wall display. Screen brightness
 * tracks how fresh the agent's thinking is; everything speeds up while the agent is working.
 */
export default function House({ agent, position, rotation = 0, onEnter, index = 0 }) {
  const { group, hovered, handlers } = useHover();
  const color = agent?.color || "#06b6d4";
  const working = agent?.status === "working";
  const glow = 0.7 + freshness(agent?.last_update) * 0.3;
  const seed = seedOf(agent?.id || String(index));
  const tickers = agent?.tickers || [];
  const sym = (i) => tickers[i % Math.max(tickers.length, 1)]?.symbol || agent?.name?.split(" ")[0]?.toUpperCase();

  return (
    <group position={position} rotation={[0, rotation, 0]}>
      <group ref={group} onClick={(e) => { e.stopPropagation(); onEnter?.(); }} {...handlers}>
        {/* plinth + front step + accent ring on the ground */}
        <mesh position={[0, BASE / 2, 0]} receiveShadow castShadow>
          <boxGeometry args={[W + 0.4, BASE, D + 0.5]} />
          <meshStandardMaterial color="#e9ecef" roughness={0.9} />
        </mesh>
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.006, 0]}>
          <ringGeometry args={[W * 0.86, W * 0.9, 64]} />
          <meshBasicMaterial color={color} transparent opacity={hovered ? 0.9 : 0.35} />
        </mesh>

        {/* interior floor */}
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, BASE + 0.002, 0]} receiveShadow>
          <planeGeometry args={[W - 0.1, D - 0.1]} />
          <meshStandardMaterial color="#efe9e1" roughness={0.8} />
        </mesh>
        {/* side walls (front and back are glass, so the studio reads from every side of the orbit) */}
        {[-1, 1].map((s) => (
          <group key={s}>
            <mesh position={[s * (W / 2), BASE + H / 2, 0]} castShadow receiveShadow><boxGeometry args={[0.08, H, D]} /><meshStandardMaterial {...WALL} /></mesh>
            {/* side window with a soft screen glow behind it */}
            <mesh position={[s * (W / 2 + 0.045), BASE + H * 0.6, -0.3]} rotation={[0, s * Math.PI / 2, 0]}>
              <planeGeometry args={[0.9, 0.5]} />
              <meshStandardMaterial color="#dbeafe" emissive={color} emissiveIntensity={working ? 0.35 : 0.12} roughness={0.1} />
            </mesh>
          </group>
        ))}
        {/* roof slab, neon fascia, rooftop solar + antenna */}
        <mesh position={[0, BASE + H + 0.07, 0]} castShadow><boxGeometry args={[W + 0.24, 0.14, D + 0.24]} /><meshStandardMaterial {...WALL} /></mesh>
        <mesh position={[0, BASE + H + 0.02, D / 2 + 0.125]}><boxGeometry args={[W + 0.24, 0.035, 0.012]} /><meshStandardMaterial color={color} emissive={color} emissiveIntensity={hovered ? 2 : 1.1} toneMapped={false} /></mesh>
        <mesh position={[-0.45, BASE + H + 0.26, -0.2]} rotation={[-0.35, 0, 0]} castShadow>
          <boxGeometry args={[0.8, 0.03, 0.55]} />
          <meshStandardMaterial color="#5b7aa6" metalness={0.5} roughness={0.25} />
        </mesh>
        <mesh position={[0.9, BASE + H + 0.4, -0.6]}><cylinderGeometry args={[0.012, 0.012, 0.55, 6]} /><meshStandardMaterial color="#9ca3af" /></mesh>
        <mesh position={[0.9, BASE + H + 0.69, -0.6]}><sphereGeometry args={[0.035, 12, 12]} /><meshStandardMaterial color={working ? "#84cc16" : color} emissive={working ? "#84cc16" : color} emissiveIntensity={1.5} toneMapped={false} /></mesh>

        {/* glass faces: corner posts, lintel, mullion, pane */}
        {[1, -1].map((f) => (
          <group key={f} position={[0, 0, f * (D / 2)]}>
            {[-1, 1].map((s) => (
              <mesh key={s} position={[s * (W / 2 - 0.04), BASE + H / 2, 0]} castShadow><boxGeometry args={[0.1, H, 0.1]} /><meshStandardMaterial color="#f1f3f5" /></mesh>
            ))}
            <mesh position={[0, BASE + H - 0.08, 0]}><boxGeometry args={[W, 0.16, 0.1]} /><meshStandardMaterial {...WALL} /></mesh>
            <mesh position={[0, BASE + 0.05, 0]}><boxGeometry args={[W, 0.1, 0.08]} /><meshStandardMaterial {...WALL} /></mesh>
            <mesh position={[f * 0.62, BASE + (H - 0.16) / 2, f * 0.01]}><boxGeometry args={[0.03, H - 0.16, 0.03]} /><meshStandardMaterial color="#cbd2da" metalness={0.5} /></mesh>
            <mesh position={[0, BASE + (H - 0.16) / 2, 0]} rotation={[0, f > 0 ? 0 : Math.PI, 0]}>
              <planeGeometry args={[W - 0.16, H - 0.16]} />
              <meshPhysicalMaterial {...GLASS} />
            </mesh>
          </group>
        ))}

        {/* interior: wall display, desk setup, robot at work */}
        <LiveMonitor position={[W / 2 - 0.08, BASE + 1.3, -0.35]} rotation={[0, -Math.PI / 2 + 0.3, 0]} width={1.1} height={0.66} color={color} seed={seed} label={agent?.name?.replace(" Analyst", "")} working={working} variant={1} glow={glow} />
        <mesh position={[0, BASE + H - 0.2, -0.1]}><boxGeometry args={[1.2, 0.02, 0.3]} /><meshStandardMaterial color="#ffffff" emissive="#ffffff" emissiveIntensity={0.8} /></mesh>
        <group position={[0, BASE, -0.1]} rotation={[0, -Math.PI / 2, 0]}>
          <Desk width={1.25} depth={0.6} height={0.5} color={color} />
          <Keyboard position={[0, 0.5, 0.2]} />
          <LiveMonitor position={[0.3, 0.83, -0.13]} rotation={[0, 0.6, 0]} width={0.5} height={0.3} color={color} seed={seed + 1} label={sym(0)} working={working} glow={glow} />
          <LiveMonitor position={[-0.28, 0.83, -0.15]} rotation={[0, 0.25, 0]} width={0.5} height={0.3} color={color} seed={seed + 2} label={sym(1)} working={working} glow={glow} />
          <CoffeeCup position={[0.48, 0.5, 0.14]} color={color} />
          <Papers position={[-0.45, 0.5, 0.08]} seed={seed} />
        </group>
        <Chair position={[-0.55, BASE, -0.1]} rotation={[0, Math.PI / 2, 0]} scale={0.75} />
        <Robot color={color} working={working} typing seated phase={index * 1.3} position={[-0.5, BASE + 0.1, -0.1]} rotation={[0, Math.PI / 2, 0]} scale={0.55} />
        <Plant position={[-1.05, BASE, -0.85]} scale={1.3} />

        {/* yard */}
        <Tree seed={seed} position={[W / 2 + 0.55, 0, D / 2 - 0.2]} />

        <Label position={[0, BASE + H + 1.05, 0]} center zIndexRange={[10, 0]}>
          <div className="house-label">
            <span className="name"><i style={{ background: color }} />{agent.name.replace(" Analyst", "")}</span>
            <div className="meta">
              <b>{agent.ticker_count}</b> tickers · conviction <b>{agent.avg_conviction ?? "–"}</b> ·{" "}
              <span className={working ? "working" : ""}>{agent.status}</span>
            </div>
          </div>
        </Label>
      </group>
    </group>
  );
}

/** Central town hall: an open pavilion with a four-sided market screen. Click to open the meeting. */
export function TownHall({ onEnter }) {
  const { group, hovered, handlers } = useHover(1.04);
  const columns = Array.from({ length: 10 }, (_, i) => (i / 10) * Math.PI * 2);
  return (
    <group ref={group} onClick={(e) => { e.stopPropagation(); onEnter?.(); }} {...handlers}>
      <mesh position={[0, 0.08, 0]} receiveShadow castShadow><cylinderGeometry args={[2.6, 2.75, 0.16, 48]} /><meshStandardMaterial color="#e9ecef" roughness={0.9} /></mesh>
      <mesh position={[0, 0.2, 0]} receiveShadow castShadow><cylinderGeometry args={[2.2, 2.3, 0.08, 48]} /><meshStandardMaterial color="#ffffff" roughness={0.6} /></mesh>
      {columns.map((a) => (
        <mesh key={a} position={[Math.sin(a) * 1.95, 1.3, Math.cos(a) * 1.95]} castShadow><cylinderGeometry args={[0.08, 0.09, 2.1, 12]} /><meshStandardMaterial color="#ffffff" roughness={0.4} /></mesh>
      ))}
      <mesh position={[0, 2.45, 0]} castShadow><cylinderGeometry args={[2.35, 2.35, 0.2, 48]} /><meshStandardMaterial color="#ffffff" roughness={0.5} /></mesh>
      <mesh position={[0, 2.38, 0]} rotation={[Math.PI / 2, 0, 0]}><torusGeometry args={[2.36, 0.025, 8, 96]} /><meshStandardMaterial color="#d946ef" emissive="#d946ef" emissiveIntensity={hovered ? 2.2 : 1.2} toneMapped={false} /></mesh>
      <mesh position={[0, 2.62, 0]} castShadow><sphereGeometry args={[1.0, 32, 16, 0, Math.PI * 2, 0, Math.PI / 2]} /><meshStandardMaterial color="#f8fafc" roughness={0.3} metalness={0.1} /></mesh>
      {/* four-sided market screen */}
      <mesh position={[0, 0.75, 0]}><boxGeometry args={[0.3, 1.1, 0.3]} /><meshStandardMaterial color="#e5e7eb" metalness={0.4} /></mesh>
      {[0, 1, 2, 3].map((i) => (
        <LiveMonitor key={i} position={[Math.sin((i * Math.PI) / 2) * 0.55, 1.5, Math.cos((i * Math.PI) / 2) * 0.55]} rotation={[0, (i * Math.PI) / 2, 0]}
          width={0.95} height={0.55} color={["#06b6d4", "#d946ef", "#84cc16", "#f59e0b"][i]} seed={100 + i} label={["SPY", "QQQ", "BTC", "DXY"][i]} variant={i % 2} />
      ))}
      <Label position={[0, 3.9, 0]} center zIndexRange={[10, 0]}>
        <div className="house-label">
          <span className="name"><i style={{ background: "#d946ef" }} />Town Hall</span>
          <div className="meta">Daily meeting · click for the boardroom</div>
        </div>
      </Label>
    </group>
  );
}

/**
 * Glass tower on Central Park South. Its lit top floor is the boardroom where the pod meets, looking out over
 * the park and the town. Click to enter the boardroom.
 */
export function BoardroomTower({ position, onEnter }) {
  const { group, hovered, handlers } = useHover(1.02);
  const H = 24; // floors below the boardroom
  const W = 6;
  const floors = Array.from({ length: Math.floor(H / 1.2) }, (_, i) => 0.6 + i * 1.2);
  return (
    <group position={position}>
      <group ref={group} onClick={(e) => { e.stopPropagation(); onEnter?.(); }} {...handlers}>
        {/* podium */}
        <mesh position={[0, 0.6, 0]} castShadow receiveShadow><boxGeometry args={[W + 2, 1.2, W + 1]} /><meshStandardMaterial color="#f1f5f9" roughness={0.7} /></mesh>
        {/* curtain wall + floor plates */}
        <mesh position={[0, H / 2 + 1.2, 0]} castShadow>
          <boxGeometry args={[W, H, W * 0.8]} />
          <meshPhysicalMaterial color="#bcd7ea" roughness={0.12} metalness={0.6} clearcoat={1} />
        </mesh>
        {floors.map((y) => (
          <mesh key={y} position={[0, y + 1.2, 0]}><boxGeometry args={[W + 0.06, 0.08, W * 0.8 + 0.06]} /><meshStandardMaterial color="#f8fafc" /></mesh>
        ))}
        {/* vertical fins */}
        {[-2, -1, 0, 1, 2].map((i) => (
          <mesh key={i} position={[i * (W / 5), H / 2 + 1.2, W * 0.4 + 0.03]}><boxGeometry args={[0.06, H, 0.06]} /><meshStandardMaterial color="#e2e8f0" metalness={0.4} /></mesh>
        ))}
        {/* boardroom floor: double height, glowing, neon fascia */}
        <group position={[0, H + 1.2, 0]}>
          <mesh position={[0, 1.2, 0]}>
            <boxGeometry args={[W + 0.3, 2.4, W * 0.8 + 0.3]} />
            <meshPhysicalMaterial color="#fff7ed" emissive="#fde68a" emissiveIntensity={hovered ? 0.9 : 0.55} transparent opacity={0.92} roughness={0.1} />
          </mesh>
          {/* table + screen silhouettes seen through the glass */}
          <mesh position={[0, 0.75, W * 0.4 + 0.16]}><boxGeometry args={[3.4, 0.12, 0.02]} /><meshStandardMaterial color="#334155" /></mesh>
          <mesh position={[0, 1.6, W * 0.4 + 0.16]}><boxGeometry args={[1.6, 0.8, 0.02]} /><meshStandardMaterial color="#0f172a" emissive="#d946ef" emissiveIntensity={0.5} /></mesh>
          <mesh position={[0, 2.45, 0]}><boxGeometry args={[W + 0.5, 0.1, W * 0.8 + 0.5]} /><meshStandardMaterial color="#d946ef" emissive="#d946ef" emissiveIntensity={hovered ? 2.2 : 1.3} toneMapped={false} /></mesh>
        </group>
        {/* crown + spire */}
        <mesh position={[0, H + 4.1, 0]} castShadow><boxGeometry args={[W * 0.7, 0.9, W * 0.55]} /><meshStandardMaterial color="#f8fafc" /></mesh>
        <mesh position={[0, H + 6.5, 0]}><cylinderGeometry args={[0.05, 0.12, 4, 8]} /><meshStandardMaterial color="#cbd5e1" metalness={0.7} /></mesh>
        <mesh position={[0, H + 8.6, 0]}><sphereGeometry args={[0.18, 16, 16]} /><meshStandardMaterial color="#d946ef" emissive="#d946ef" emissiveIntensity={2} toneMapped={false} /></mesh>
        <Label position={[0, H + 10, 0]} center zIndexRange={[10, 0]}>
          <div className="house-label">
            <span className="name"><i style={{ background: "#d946ef" }} />Boardroom</span>
            <div className="meta">Pod meetings over Central Park · click to enter</div>
          </div>
        </Label>
      </group>
    </group>
  );
}
