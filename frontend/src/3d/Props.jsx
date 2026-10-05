import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import { rng, useScreenTexture } from "./screens.js";
import * as THREE from "three";

/** Monitor on a stand. Pass `texture` for live content, or `children` to render custom content on the screen. */
export function Monitor({ texture, width = 0.62, height = 0.38, glow = 1, children, ...props }) {
  return (
    <group {...props}>
      <mesh position={[0, 0, -0.018]} castShadow>
        <boxGeometry args={[width + 0.04, height + 0.04, 0.03]} />
        <meshStandardMaterial color="#e5e7eb" metalness={0.4} roughness={0.35} />
      </mesh>
      <mesh>
        <planeGeometry args={[width, height]} />
        {texture ? <meshBasicMaterial map={texture} toneMapped={false} color={[glow, glow, glow]} /> : <meshBasicMaterial color="#f8fafc" />}
      </mesh>
      {children}
      {/* stand */}
      <mesh position={[0, -height / 2 - 0.08, -0.04]}><boxGeometry args={[0.04, 0.16, 0.03]} /><meshStandardMaterial color="#cbd2da" metalness={0.6} roughness={0.3} /></mesh>
      <mesh position={[0, -height / 2 - 0.16, -0.03]}><boxGeometry args={[0.2, 0.012, 0.12]} /><meshStandardMaterial color="#cbd2da" metalness={0.6} roughness={0.3} /></mesh>
    </group>
  );
}

/** Monitor with a live chart texture. */
export function LiveMonitor({ color, seed, label, working, variant, glow, ...props }) {
  const texture = useScreenTexture({ color, seed, label, working, variant });
  return <Monitor texture={texture} glow={glow} {...props} />;
}

export function Keyboard(props) {
  return (
    <group {...props}>
      <mesh castShadow><boxGeometry args={[0.42, 0.018, 0.14]} /><meshStandardMaterial color="#f3f4f6" roughness={0.5} /></mesh>
      {/* key rows */}
      {[-0.04, 0, 0.04].map((z) => (
        <mesh key={z} position={[0, 0.011, z]}><boxGeometry args={[0.38, 0.006, 0.026]} /><meshStandardMaterial color="#d1d5db" /></mesh>
      ))}
      {/* mouse */}
      <mesh position={[0.29, 0.006, 0.02]} scale={[1, 0.5, 1.4]} castShadow><sphereGeometry args={[0.03, 16, 12]} /><meshStandardMaterial color="#f3f4f6" /></mesh>
    </group>
  );
}

/** Coffee mug with a faint rising wisp. */
export function CoffeeCup({ color = "#06b6d4", ...props }) {
  const steam = useRef();
  useFrame(({ clock }) => {
    if (!steam.current) return;
    const t = (clock.elapsedTime * 0.5) % 1;
    steam.current.position.y = 0.08 + t * 0.1;
    steam.current.material.opacity = 0.35 * (1 - t);
    steam.current.scale.setScalar(0.6 + t);
  });
  return (
    <group {...props}>
      <mesh position={[0, 0.04, 0]} castShadow><cylinderGeometry args={[0.035, 0.03, 0.08, 20]} /><meshStandardMaterial color="#ffffff" roughness={0.25} /></mesh>
      <mesh position={[0, 0.045, 0]}><cylinderGeometry args={[0.036, 0.036, 0.018, 20]} /><meshStandardMaterial color={color} /></mesh>
      <mesh position={[0, 0.079, 0]} rotation={[-Math.PI / 2, 0, 0]}><circleGeometry args={[0.03, 20]} /><meshStandardMaterial color="#6b4423" /></mesh>
      <mesh position={[0.042, 0.04, 0]} rotation={[0, 0, Math.PI / 2]}><torusGeometry args={[0.018, 0.006, 8, 16]} /><meshStandardMaterial color="#ffffff" /></mesh>
      <mesh ref={steam} position={[0, 0.1, 0]}><sphereGeometry args={[0.02, 8, 8]} /><meshBasicMaterial color="#ffffff" transparent opacity={0.3} depthWrite={false} /></mesh>
    </group>
  );
}

/** A loose stack of research papers (with printed "lines"). */
export function Papers({ seed = 1, count = 4, ...props }) {
  const sheets = useMemo(() => {
    const r = rng(seed);
    return Array.from({ length: count }, (_, i) => ({ x: (r() - 0.5) * 0.22, z: (r() - 0.5) * 0.14, rot: (r() - 0.5) * 0.9, y: i * 0.003 }));
  }, [seed, count]);
  return (
    <group {...props}>
      {sheets.map((s, i) => (
        <group key={i} position={[s.x, s.y, s.z]} rotation={[0, s.rot, 0]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow><planeGeometry args={[0.16, 0.21]} /><meshStandardMaterial color="#ffffff" roughness={0.9} /></mesh>
          {[0.06, 0.035, 0.01, -0.015, -0.04].map((z, j) => (
            <mesh key={j} rotation={[-Math.PI / 2, 0, 0]} position={[-0.01 + (j % 2) * 0.01, 0.0008, z]}>
              <planeGeometry args={[j === 0 ? 0.09 : 0.12, j === 0 ? 0.012 : 0.006]} />
              <meshBasicMaterial color={j === 0 ? "#1a1a1a" : "#9ca3af"} />
            </mesh>
          ))}
        </group>
      ))}
    </group>
  );
}

export function Plant(props) {
  return (
    <group {...props}>
      <mesh position={[0, 0.07, 0]} castShadow><cylinderGeometry args={[0.07, 0.055, 0.14, 16]} /><meshStandardMaterial color="#ffffff" roughness={0.4} /></mesh>
      {[[0, 0.24, 0, 0.09], [0.05, 0.2, 0.03, 0.07], [-0.05, 0.19, -0.02, 0.07]].map(([x, y, z, r], i) => (
        <mesh key={i} position={[x, y, z]} castShadow><icosahedronGeometry args={[r, 0]} /><meshStandardMaterial color="#65a30d" roughness={0.8} flatShading /></mesh>
      ))}
    </group>
  );
}

export function Chair({ color = "#1f2937", back = true, ...props }) {
  return (
    <group {...props}>
      <mesh position={[0, 0.4, 0]} castShadow><boxGeometry args={[0.42, 0.06, 0.42]} /><meshStandardMaterial color={color} roughness={0.6} /></mesh>
      {back && <mesh position={[0, 0.7, -0.2]} castShadow><boxGeometry args={[0.42, 0.5, 0.05]} /><meshStandardMaterial color={color} roughness={0.6} /></mesh>}
      <mesh position={[0, 0.2, 0]}><cylinderGeometry args={[0.025, 0.025, 0.36, 8]} /><meshStandardMaterial color="#9ca3af" metalness={0.7} /></mesh>
      <mesh position={[0, 0.03, 0]}><cylinderGeometry args={[0.2, 0.2, 0.03, 5]} /><meshStandardMaterial color="#9ca3af" metalness={0.7} /></mesh>
    </group>
  );
}

/** White desk with an accent edge. Top surface sits at `height`. */
export function Desk({ width = 1.6, depth = 0.7, height = 0.74, color = "#06b6d4", ...props }) {
  return (
    <group {...props}>
      <mesh position={[0, height - 0.02, 0]} castShadow receiveShadow><boxGeometry args={[width, 0.04, depth]} /><meshStandardMaterial color="#ffffff" roughness={0.35} /></mesh>
      <mesh position={[0, height - 0.02, depth / 2 + 0.003]}><boxGeometry args={[width, 0.012, 0.006]} /><meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.6} /></mesh>
      {[-1, 1].map((s) => (
        <mesh key={s} position={[s * (width / 2 - 0.04), (height - 0.04) / 2, 0]} castShadow><boxGeometry args={[0.04, height - 0.04, depth - 0.06]} /><meshStandardMaterial color="#e5e7eb" metalness={0.4} roughness={0.3} /></mesh>
      ))}
    </group>
  );
}

/** Bookshelf of research binders in agent color tones. */
export function Bookshelf({ color = "#06b6d4", seed = 1, ...props }) {
  const books = useMemo(() => {
    const r = rng(seed);
    return [0, 1, 2, 3].flatMap((shelf) => {
      const row = [];
      for (let x = -0.42; x < 0.4; x += 0.05 + r() * 0.04) {
        const h = 0.2 + r() * 0.1;
        row.push({ x, y: 0.12 + shelf * 0.38 + h / 2, h, tone: r() < 0.3 ? color : ["#e5e7eb", "#cbd5e1", "#94a3b8", "#f8fafc", "#fde68a"][Math.floor(r() * 5)] });
      }
      return row;
    });
  }, [seed, color]);
  return (
    <group {...props}>
      <mesh position={[0, 0.78, -0.02]} castShadow><boxGeometry args={[1, 1.56, 0.04]} /><meshStandardMaterial color="#f3f4f6" /></mesh>
      {[0, 1, 2, 3, 4].map((i) => (
        <mesh key={i} position={[0, 0.1 + i * 0.38, 0.12]} castShadow><boxGeometry args={[1, 0.03, 0.28]} /><meshStandardMaterial color="#ffffff" /></mesh>
      ))}
      {[-0.5, 0.5].map((x) => (
        <mesh key={x} position={[x, 0.78, 0.12]} castShadow><boxGeometry args={[0.03, 1.56, 0.28]} /><meshStandardMaterial color="#ffffff" /></mesh>
      ))}
      {books.map((b, i) => (
        <mesh key={i} position={[b.x, b.y, 0.12]} castShadow><boxGeometry args={[0.04, b.h, 0.22]} /><meshStandardMaterial color={b.tone} roughness={0.7} /></mesh>
      ))}
    </group>
  );
}

/** Whiteboard with marker scribbles: a chart, arrows and bullet lines. */
export function Whiteboard({ color = "#06b6d4", seed = 1, ...props }) {
  const texture = useMemo(() => {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 320;
    const g = c.getContext("2d");
    g.fillStyle = "#ffffff";
    g.fillRect(0, 0, 512, 320);
    const r = rng(seed);
    g.lineCap = "round";
    g.lineJoin = "round";
    g.strokeStyle = "#1f2937";
    g.lineWidth = 3;
    g.beginPath(); g.moveTo(30, 40); g.lineTo(30, 200); g.lineTo(260, 200); g.stroke();
    g.strokeStyle = color;
    g.lineWidth = 5;
    g.beginPath();
    let y = 170;
    for (let x = 40; x <= 250; x += 21) { y = Math.max(50, Math.min(190, y - 12 + r() * 20)); if (x === 40) g.moveTo(x, y); else g.lineTo(x, y); }
    g.stroke();
    g.strokeStyle = "#dc2626";
    g.setLineDash([10, 8]);
    g.beginPath(); g.moveTo(30, 90); g.lineTo(260, 90); g.stroke();
    g.setLineDash([]);
    g.strokeStyle = "#374151";
    g.lineWidth = 4;
    for (let i = 0; i < 6; i += 1) {
      const ly = 50 + i * 38;
      g.beginPath(); g.arc(300, ly, 4, 0, Math.PI * 2); g.stroke();
      g.beginPath(); g.moveTo(316, ly); g.lineTo(330 + r() * 150, ly + (r() - 0.5) * 4); g.stroke();
    }
    g.strokeStyle = "#059669";
    g.beginPath(); g.moveTo(60, 270); g.quadraticCurveTo(180, 230, 300, 280); g.lineTo(288, 268); g.moveTo(300, 280); g.lineTo(284, 286); g.stroke();
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }, [color, seed]);
  return (
    <group {...props}>
      <mesh position={[0, 0, -0.015]}><boxGeometry args={[1.66, 1.06, 0.03]} /><meshStandardMaterial color="#d1d5db" metalness={0.5} /></mesh>
      <mesh><planeGeometry args={[1.6, 1]} /><meshStandardMaterial map={texture} roughness={0.35} /></mesh>
      <mesh position={[0, -0.55, 0.04]}><boxGeometry args={[1.2, 0.03, 0.08]} /><meshStandardMaterial color="#d1d5db" /></mesh>
    </group>
  );
}

/** Desk lamp with a warm glow. */
export function DeskLamp(props) {
  return (
    <group {...props}>
      <mesh position={[0, 0.01, 0]}><cylinderGeometry args={[0.07, 0.08, 0.02, 20]} /><meshStandardMaterial color="#e5e7eb" metalness={0.5} /></mesh>
      <mesh position={[0, 0.17, 0]} rotation={[0, 0, 0.25]}><cylinderGeometry args={[0.01, 0.01, 0.34, 8]} /><meshStandardMaterial color="#d1d5db" metalness={0.6} /></mesh>
      <mesh position={[-0.08, 0.33, 0]} rotation={[0, 0, -1.1]}><coneGeometry args={[0.07, 0.12, 20, 1, true]} /><meshStandardMaterial color="#f9fafb" emissive="#fde68a" emissiveIntensity={0.6} side={THREE.DoubleSide} /></mesh>
      <pointLight position={[-0.12, 0.28, 0]} intensity={0.25} distance={1.2} color="#fde68a" />
    </group>
  );
}
