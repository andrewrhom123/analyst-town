import { ContactShadows, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import * as THREE from "three";
import { useEffect, useMemo } from "react";
import { Chair, CoffeeCup, Desk, Keyboard, LiveMonitor, Papers, Plant } from "./Props.jsx";
import Robot from "./Robot.jsx";
import { rng, seedOf } from "./screens.js";

const fmtChg = (v) => (v == null ? "" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);

/** Stable illustrative sparkline for a ticker screen, trending with today's move. */
function sparkline(symbol, change) {
  const r = rng(seedOf(symbol));
  const drift = change == null ? 0 : Math.max(-1, Math.min(1, change / 3));
  let v = 50;
  return Array.from({ length: 24 }, () => (v = Math.max(8, Math.min(92, v + (r() - 0.5) * 14 + drift * 2.2))));
}

/** Screen content drawn into a canvas texture: symbol, today's move, sparkline, current signal. */
function useTickerTexture(ticker, selected, color) {
  const texture = useMemo(() => {
    const W = 440, H = 264;
    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");
    const up = ticker.change_pct == null ? null : ticker.change_pct >= 0;
    const tone = up == null ? "#6b7280" : up ? "#059669" : "#dc2626";
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = selected ? color : "#e5e7eb";
    ctx.fillRect(0, 0, W, 8);
    ctx.fillStyle = "#1a1a1a";
    ctx.font = "bold 52px 'Roboto Mono', monospace";
    ctx.fillText(ticker.symbol, 24, 74);
    ctx.fillStyle = tone;
    ctx.font = "600 34px 'Roboto Mono', monospace";
    const chg = fmtChg(ticker.change_pct) || "—";
    ctx.fillText(chg, W - 24 - ctx.measureText(chg).width, 70);
    const pts = sparkline(ticker.symbol, ticker.change_pct);
    const lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || 1;
    const x = (i) => 24 + (i / (pts.length - 1)) * (W - 48);
    const y = (p) => 200 - ((p - lo) / span) * 96;
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p)) : ctx.moveTo(x(i), y(p))));
    ctx.lineTo(x(pts.length - 1), 204);
    ctx.lineTo(24, 204);
    ctx.closePath();
    ctx.fillStyle = up === false ? "rgba(220,38,38,0.10)" : "rgba(5,150,105,0.10)";
    ctx.fill();
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p)) : ctx.moveTo(x(i), y(p))));
    ctx.strokeStyle = tone;
    ctx.lineWidth = 4;
    ctx.lineJoin = "round";
    ctx.stroke();
    ctx.fillStyle = "#6b7280";
    ctx.font = "bold 22px Inter, Arial, sans-serif";
    ctx.fillText((ticker.signal || "research queued").toUpperCase(), 24, 244);
    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 8;
    return tex;
  }, [ticker.symbol, ticker.change_pct, ticker.signal, selected, color]);
  useEffect(() => () => texture.dispose(), [texture]);
  return texture;
}

function Screen({ ticker, position, rotation, selected, color, onSelect }) {
  const texture = useTickerTexture(ticker, selected, color);
  return (
    <group position={position} rotation={rotation}>
      {/* bezel: selected = neon frame */}
      <mesh position={[0, 0, -0.03]} castShadow>
        <boxGeometry args={[1.2, 0.76, 0.05]} />
        <meshStandardMaterial color={selected ? color : "#e5e7eb"} emissive={selected ? color : "#000"} emissiveIntensity={selected ? 0.8 : 0} metalness={0.3} roughness={0.35} />
      </mesh>
      <mesh
        onClick={(e) => { e.stopPropagation(); onSelect(); }}
        onPointerOver={(e) => { e.stopPropagation(); document.body.style.cursor = "pointer"; }}
        onPointerOut={() => { document.body.style.cursor = ""; }}
      >
        <planeGeometry args={[1.1, 0.66]} />
        <meshBasicMaterial map={texture} toneMapped={false} />
      </mesh>
      <mesh position={[0, -0.52, -0.06]}><boxGeometry args={[0.06, 0.3, 0.04]} /><meshStandardMaterial color="#cbd2da" metalness={0.6} /></mesh>
    </group>
  );
}

/** Bright modern office: the robot typing at its desk, a wall of ticker monitors (click to switch), research strewn about. */
export default function OfficeScene({ agent, tickers, selected, onSelect }) {
  const color = agent?.color || "#06b6d4";
  const working = agent?.status === "working";
  const seed = seedOf(agent?.id || "office");
  const perRow = Math.min(4, Math.max(1, Math.ceil(tickers.length / 2)));
  const screens = tickers.map((t, i) => {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const count = Math.min(perRow, tickers.length - row * perRow);
    const spread = (col - (count - 1) / 2) * 1.28;
    return { t, position: [spread, 1.75 + (1 - row) * 0.84, -1.35 + Math.abs(spread) * 0.18], rotation: [0, -spread * 0.22, 0] };
  });

  return (
    <Canvas shadows dpr={[1, 2]} camera={{ position: [1.3, 2.3, 4.4], fov: 44 }} gl={{ antialias: true, toneMapping: THREE.NeutralToneMapping }}>
      <color attach="background" args={["#f8fafc"]} />
      <ambientLight intensity={0.7} />
      <hemisphereLight args={["#ffffff", "#e7e1d8", 0.7]} />
      <directionalLight position={[3, 6, 4]} intensity={1.3} castShadow shadow-mapSize={[1024, 1024]} shadow-bias={-0.0004} />
      <pointLight position={[0, 2.4, 0.6]} intensity={0.8} color={color} distance={4} />

      {/* room: warm light floor, white walls, a window strip of sky */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[14, 10]} />
        <meshStandardMaterial color="#ede7df" roughness={0.85} />
      </mesh>
      <mesh position={[0, 2.5, -2]} receiveShadow>
        <planeGeometry args={[14, 6]} />
        <meshStandardMaterial color="#ffffff" roughness={0.9} />
      </mesh>
      <mesh position={[-4.6, 2.6, -1.98]}>
        <planeGeometry args={[2.4, 2.2]} />
        <meshBasicMaterial color="#bae6fd" />
      </mesh>
      <mesh position={[4.6, 2.6, -1.98]}>
        <planeGeometry args={[2.4, 2.2]} />
        <meshBasicMaterial color="#bae6fd" />
      </mesh>
      <mesh position={[0, 0.06, -1.97]}><boxGeometry args={[14, 0.12, 0.04]} /><meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.4} /></mesh>

      {/* desk setup */}
      <Desk position={[0, 0, 0.2]} width={3.2} depth={1.1} height={0.76} color={color} />
      <Keyboard position={[0, 0.76, 0.6]} scale={1.3} />
      <LiveMonitor position={[-0.95, 1.2, 0.0]} rotation={[0, 0.35, 0]} width={0.7} height={0.42} color={color} seed={seed + 3} label="RESEARCH" working={working} />
      <LiveMonitor position={[0.95, 1.2, 0.0]} rotation={[0, -0.35, 0]} width={0.7} height={0.42} color={color} seed={seed + 4} label="MODEL" working={working} variant={1} />
      <CoffeeCup position={[1.25, 0.76, 0.5]} color={color} scale={1.4} />
      <Papers position={[-1.15, 0.76, 0.45]} seed={seed} count={6} scale={1.5} />
      <Papers position={[0.62, 0.76, 0.6]} seed={seed + 9} count={3} scale={1.5} />
      <Plant position={[1.45, 0.76, -0.1]} scale={1.5} />
      <Plant position={[-3.2, 0, -1.2]} scale={3} />

      {/* the analyst, seated and typing, facing the screen wall */}
      <Chair position={[0, 0, 1.1]} rotation={[0, Math.PI, 0]} scale={1.3} color="#374151" back={false} />
      <Robot color={color} working={working} typing seated position={[0, 0.3, 1.0]} rotation={[0, Math.PI, 0]} scale={0.7} />

      {screens.map(({ t, position, rotation }) => (
        <Screen key={t.symbol} ticker={t} position={position} rotation={rotation} color={color}
          selected={t.symbol === selected} onSelect={() => onSelect(t.symbol)} />
      ))}
      <ContactShadows position={[0, 0.005, 0]} opacity={0.3} scale={10} blur={2.2} far={3} />
      <OrbitControls enablePan={false} enableZoom={false} minPolarAngle={1.0} maxPolarAngle={1.45}
        minAzimuthAngle={-0.6} maxAzimuthAngle={0.6} target={[0, 1.5, -0.5]} />
    </Canvas>
  );
}
