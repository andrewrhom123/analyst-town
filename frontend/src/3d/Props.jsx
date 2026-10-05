import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import { rng, useScreenTexture } from "./screens.js";

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
