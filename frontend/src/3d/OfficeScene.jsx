import { Html, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useMemo } from "react";
import * as THREE from "three";
import Robot from "./Robot.jsx";

const fmtChg = (v) => (v == null ? "" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);

function Screen({ ticker, position, rotation, selected, color, onSelect }) {
  // Dark panel with a faint agent tint (stronger when selected) so the text stays readable.
  const panel = useMemo(() => new THREE.Color("#050b18").lerp(new THREE.Color(color), selected ? 0.16 : 0.05), [color, selected]);
  const up = ticker.change_pct == null ? null : ticker.change_pct >= 0;
  return (
    <group position={position} rotation={rotation}>
      {/* bezel (selected = neon frame) */}
      <mesh position={[0, 0, -0.03]}>
        <boxGeometry args={[1.18, 0.74, 0.04]} />
        <meshStandardMaterial color={selected ? color : "#1a2440"} emissive={selected ? color : "#000"} emissiveIntensity={selected ? 0.9 : 0} />
      </mesh>
      <mesh onClick={(e) => { e.stopPropagation(); onSelect(); }}>
        <planeGeometry args={[1.1, 0.66]} />
        <meshBasicMaterial color={panel} />
      </mesh>
      <Html transform distanceFactor={1.6} position={[0, 0, 0.02]} zIndexRange={[5, 0]}>
        <button
          onClick={onSelect}
          aria-pressed={selected}
          aria-label={`Show ${ticker.symbol}`}
          style={{
            width: 168, height: 100, border: 0, background: "transparent", color: "#e6edf7", cursor: "pointer",
            fontFamily: "Roboto Mono, monospace", display: "grid", placeItems: "center", alignContent: "center", gap: 2,
          }}
        >
          <span style={{ fontSize: 26, fontWeight: 600 }}>{ticker.symbol}</span>
          <span style={{ fontSize: 16, color: up == null ? "#6f7d96" : up ? "#4ade80" : "#f87171" }}>{fmtChg(ticker.change_pct)}</span>
          <span style={{ fontSize: 12, color: "#a9b6cc", textTransform: "uppercase" }}>{ticker.signal || "research queued"}</span>
        </button>
      </Html>
    </group>
  );
}

/** The agent's office: robot at its desk, one monitor per covered ticker (click to switch). */
export default function OfficeScene({ agent, tickers, selected, onSelect }) {
  const color = agent?.color || "#22d3ee";
  const perRow = Math.min(4, Math.max(1, Math.ceil(tickers.length / 2)));
  const screens = tickers.map((t, i) => {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const count = Math.min(perRow, tickers.length - row * perRow);
    const spread = (col - (count - 1) / 2) * 1.25;
    const angle = -spread * 0.22;
    return { t, position: [spread, 1.55 + (1 - row) * 0.82, -1.25 + Math.abs(spread) * 0.18], rotation: [0, angle, 0] };
  });

  return (
    <Canvas dpr={[1, 2]} camera={{ position: [0, 2.5, 4.6], fov: 44 }} gl={{ antialias: true }}>
      <color attach="background" args={["#08101f"]} />
      <ambientLight intensity={0.45} />
      <pointLight position={[0, 3, 2]} intensity={14} color={color} distance={10} />
      <directionalLight position={[3, 5, 4]} intensity={0.6} />
      {/* floor + back wall */}
      <mesh rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[14, 10]} />
        <meshStandardMaterial color="#0b1224" />
      </mesh>
      <mesh position={[0, 2.5, -2]}>
        <planeGeometry args={[14, 6]} />
        <meshStandardMaterial color="#0d1630" />
      </mesh>
      {/* desk */}
      <mesh position={[0, 0.72, 0.2]}>
        <boxGeometry args={[3.2, 0.08, 1.1]} />
        <meshStandardMaterial color="#1b2647" metalness={0.4} roughness={0.5} />
      </mesh>
      <mesh position={[0, 0.735, 0.76]}>
        <boxGeometry args={[3.22, 0.03, 0.02]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.5} />
      </mesh>
      {[-1.45, 1.45].map((x) => (
        <mesh key={x} position={[x, 0.36, 0.2]}>
          <boxGeometry args={[0.08, 0.72, 0.9]} />
          <meshStandardMaterial color="#141c33" />
        </mesh>
      ))}
      {/* the analyst, seated behind the desk facing the screens */}
      <Robot color={color} working={agent?.status === "working"} seated position={[0, 0.3, 0.55]} rotation={[0, Math.PI, 0]} scale={0.62} />
      {screens.map(({ t, position, rotation }) => (
        <Screen key={t.symbol} ticker={t} position={position} rotation={rotation} color={color}
          selected={t.symbol === selected} onSelect={() => onSelect(t.symbol)} />
      ))}
      <OrbitControls enablePan={false} enableZoom={false} minPolarAngle={1.05} maxPolarAngle={1.45}
        minAzimuthAngle={-0.5} maxAzimuthAngle={0.5} target={[0, 1.6, -0.6]} />
    </Canvas>
  );
}
