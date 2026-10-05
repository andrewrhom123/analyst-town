import { useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { rng } from "./screens.js";

/*
 * Central Park, procedurally: rolling lawns, instanced trees, The Lake with Bow Bridge, winding paths and a
 * ring of Manhattan towers (taller along the south edge, like Billionaires' Row). Shared by the town and the
 * boardroom window. Everything is instanced so it stays cheap.
 */

export const PARK = { halfX: 58, halfZ: 34 }; // park rectangle; the skyline sits just outside it
export const LAKE = { x: -24, z: -15, r: 7.5 };

const TREE_COLORS = ["#65a30d", "#4d7c0f", "#84cc16", "#16a34a", "#22c55e", "#a3e635", "#15803d"];

/** Flat ribbon along a smooth curve (paths, drives). */
function ribbonGeometry(points, width, segments = 120) {
  const curve = new THREE.CatmullRomCurve3(points.map(([x, z]) => new THREE.Vector3(x, 0, z)));
  const pos = [];
  const idx = [];
  for (let i = 0; i <= segments; i += 1) {
    const t = i / segments;
    const p = curve.getPointAt(t);
    const tan = curve.getTangentAt(t);
    const nx = -tan.z * (width / 2);
    const nz = tan.x * (width / 2);
    pos.push(p.x + nx, 0, p.z + nz, p.x - nx, 0, p.z - nz);
    if (i < segments) {
      const a = i * 2;
      idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

/** Irregular lake outline. */
function lakeShape(r, seed) {
  const rand = rng(seed);
  const shape = new THREE.Shape();
  const n = 28;
  const wobble = Array.from({ length: 5 }, () => rand() * Math.PI * 2);
  for (let i = 0; i <= n; i += 1) {
    const a = (i / n) * Math.PI * 2;
    const k = 1 + 0.18 * Math.sin(a * 2 + wobble[0]) + 0.1 * Math.sin(a * 3 + wobble[1]) + 0.06 * Math.sin(a * 5 + wobble[2]);
    const x = Math.cos(a) * r * 1.5 * k;
    const y = Math.sin(a) * r * 0.75 * k;
    if (i === 0) shape.moveTo(x, y); else shape.lineTo(x, y);
  }
  return shape;
}

function Instanced({ geometry, material, items, castShadow = false }) {
  const ref = useRef();
  useLayoutEffect(() => {
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const color = new THREE.Color();
    items.forEach((it, i) => {
      q.setFromEuler(new THREE.Euler(0, it.rot || 0, 0));
      m.compose(new THREE.Vector3(...it.pos), q, new THREE.Vector3(...it.scale));
      ref.current.setMatrixAt(i, m);
      if (it.color) ref.current.setColorAt(i, color.set(it.color));
    });
    ref.current.instanceMatrix.needsUpdate = true;
    if (ref.current.instanceColor) ref.current.instanceColor.needsUpdate = true;
    ref.current.computeBoundingSphere();
  }, [items]);
  return <instancedMesh ref={ref} args={[geometry, material, items.length]} castShadow={castShadow} receiveShadow />;
}

/** Scatter trees across the park, keeping clear of `clearings` ([{x, z, r}]), the lake and the paths' center. */
export function Trees({ count = 260, seed = 7, clearings = [], area = PARK }) {
  const { trunks, crowns, tops } = useMemo(() => {
    const rand = rng(seed);
    const blocked = [...clearings, { x: LAKE.x, z: LAKE.z, r: LAKE.r * 1.55 }];
    const trunksOut = [];
    const crownsOut = [];
    const topsOut = [];
    let guard = 0;
    while (trunksOut.length < count && guard < count * 20) {
      guard += 1;
      // clustered groves: bias toward a few grove centers
      const x = (rand() * 2 - 1) * (area.halfX - 2);
      const z = (rand() * 2 - 1) * (area.halfZ - 2);
      if (blocked.some((c) => Math.hypot(x - c.x, z - c.z) < c.r)) continue;
      const grove = Math.sin(x * 0.11) + Math.cos(z * 0.13) + Math.sin((x + z) * 0.05);
      if (grove < -0.4 && rand() < 0.75) continue; // open meadows between groves
      const h = 1.2 + rand() * 1.6;
      const w = 0.9 + rand() * 0.9;
      const color = TREE_COLORS[Math.floor(rand() * TREE_COLORS.length)];
      trunksOut.push({ pos: [x, h / 2, z], scale: [1, h, 1] });
      crownsOut.push({ pos: [x, h + w * 0.55, z], scale: [w, w * (0.9 + rand() * 0.4), w], rot: rand() * 6, color });
      if (rand() < 0.45) topsOut.push({ pos: [x + (rand() - 0.5) * 0.4, h + w * 1.15, z + (rand() - 0.5) * 0.4], scale: [w * 0.6, w * 0.6, w * 0.6], rot: rand() * 6, color });
    }
    return { trunks: trunksOut, crowns: crownsOut, tops: topsOut };
  }, [count, seed, clearings, area]);

  const geo = useMemo(() => ({
    trunk: new THREE.CylinderGeometry(0.07, 0.11, 1, 6),
    crown: new THREE.IcosahedronGeometry(0.75, 1),
  }), []);
  const mat = useMemo(() => ({
    trunk: new THREE.MeshStandardMaterial({ color: "#8b7355", roughness: 1 }),
    crown: new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.9, flatShading: true }),
  }), []);

  return (
    <group>
      <Instanced geometry={geo.trunk} material={mat.trunk} items={trunks} castShadow />
      <Instanced geometry={geo.crown} material={mat.crown} items={crowns} castShadow />
      <Instanced geometry={geo.crown} material={mat.crown} items={tops} castShadow />
    </group>
  );
}

/** The Lake with a Bow Bridge across its narrow waist. */
export function Lake() {
  const shape = useMemo(() => lakeShape(LAKE.r, 11), []);
  const bridge = useMemo(() => {
    const curve = new THREE.QuadraticBezierCurve3(new THREE.Vector3(-2.2, 0.05, 0), new THREE.Vector3(0, 1.0, 0), new THREE.Vector3(2.2, 0.05, 0));
    return new THREE.TubeGeometry(curve, 24, 0.07, 6, false);
  }, []);
  return (
    <group position={[LAKE.x, 0, LAKE.z]}>
      {/* shoreline rim then water */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.004, 0]} scale={1.06}>
        <shapeGeometry args={[shape]} />
        <meshStandardMaterial color="#d6d3c4" roughness={1} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.012, 0]} receiveShadow>
        <shapeGeometry args={[shape]} />
        <meshPhysicalMaterial color="#7cc4e8" roughness={0.08} metalness={0.1} clearcoat={1} clearcoatRoughness={0.1} />
      </mesh>
      {/* Bow Bridge: deck arch, railing and a cast-iron accent */}
      <group position={[2.5, 0, 0.6]} rotation={[0, Math.PI / 2.4, 0]}>
        {[-0.35, 0.35].map((z) => (
          <mesh key={z} geometry={bridge} position={[0, 0, z]} castShadow><meshStandardMaterial color="#f5f5f4" roughness={0.5} /></mesh>
        ))}
        {[-0.35, 0.35].map((z) => (
          <mesh key={`r${z}`} geometry={bridge} position={[0, 0.28, z]} scale={[1, 0.85, 1]}><meshStandardMaterial color="#e7e5e4" roughness={0.5} /></mesh>
        ))}
        {Array.from({ length: 9 }, (_, i) => {
          const x = -1.8 + i * 0.45;
          const y = 1.0 * (1 - (x / 2.2) ** 2);
          return <mesh key={i} position={[x, y / 2 + 0.02, 0]} castShadow><boxGeometry args={[0.38, y + 0.04, 0.72]} /><meshStandardMaterial color="#fafaf9" roughness={0.6} /></mesh>;
        })}
      </group>
      {/* rowboats */}
      {[[-4, -1.5, 0.4], [3.5, 2.2, 2.1], [-1, 2.5, 1.2]].map(([x, z, r], i) => (
        <mesh key={i} position={[x, 0.06, z]} rotation={[0, r, 0]} scale={[1, 0.35, 0.42]} castShadow>
          <sphereGeometry args={[0.45, 12, 8, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2]} />
          <meshStandardMaterial color={["#06b6d4", "#d946ef", "#f59e0b"][i]} roughness={0.5} side={THREE.DoubleSide} />
        </mesh>
      ))}
    </group>
  );
}

/** Lawns, the park drive loop and footpaths. `paths` are extra [[x,z],...] polylines (e.g. to each house). */
export function Grounds({ paths = [] }) {
  const loop = useMemo(() => ribbonGeometry([
    [-50, -26], [-10, -30], [30, -28], [52, -18], [54, 10], [40, 27], [0, 29], [-38, 27], [-54, 8], [-54, -16], [-50, -26],
  ], 1.6, 240), []);
  const walks = useMemo(() => [
    [[-13, 3], [-20, 6], [-30, 3], [-38, -6], [-34, -18]],
    [[12, -3], [20, -10], [30, -12], [42, -6]],
    [[3, 13], [5, 20], [-2, 26]],
    [[-6, -12], [-12, -22], [-16, -28]],
    ...paths,
  ].map((p) => ribbonGeometry(p, 0.9, 80)), [paths]);
  return (
    <group>
      {/* city floor, then the park lawn with a few rolling knolls */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.05, 0]} receiveShadow>
        <planeGeometry args={[400, 400]} />
        <meshStandardMaterial color="#d9dde2" roughness={1} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.03, 0]} receiveShadow>
        <planeGeometry args={[PARK.halfX * 2, PARK.halfZ * 2]} />
        <meshStandardMaterial color="#9fd36b" roughness={1} />
      </mesh>
      {[[30, 14, 7], [-36, 16, 9], [36, -20, 6], [8, -24, 5]].map(([x, z, r], i) => (
        <mesh key={i} position={[x, -r * 0.94, z]} receiveShadow>
          <sphereGeometry args={[r, 32, 16]} />
          <meshStandardMaterial color={i % 2 ? "#a7d977" : "#93c95e"} roughness={1} />
        </mesh>
      ))}
      {/* low stone wall along Fifth/Central Park West */}
      {[1, -1].map((s) => (
        <mesh key={`wz${s}`} position={[0, 0.2, s * PARK.halfZ]}><boxGeometry args={[PARK.halfX * 2, 0.4, 0.3]} /><meshStandardMaterial color="#c8c2b4" roughness={1} /></mesh>
      ))}
      {[1, -1].map((s) => (
        <mesh key={`wx${s}`} position={[s * PARK.halfX, 0.2, 0]}><boxGeometry args={[0.3, 0.4, PARK.halfZ * 2]} /><meshStandardMaterial color="#c8c2b4" roughness={1} /></mesh>
      ))}
      <mesh geometry={loop} position={[0, -0.015, 0]} receiveShadow><meshStandardMaterial color="#cfd4da" roughness={1} /></mesh>
      {walks.map((g, i) => (
        <mesh key={i} geometry={g} position={[0, -0.012, 0]} receiveShadow><meshStandardMaterial color="#ece4d4" roughness={1} /></mesh>
      ))}
    </group>
  );
}

/** Window grid texture shared by every tower. */
function useWindowTexture() {
  return useMemo(() => {
    const c = document.createElement("canvas");
    c.width = 64;
    c.height = 128;
    const g = c.getContext("2d");
    g.fillStyle = "#ffffff";
    g.fillRect(0, 0, 64, 128);
    const rand = rng(3);
    for (let y = 4; y < 128; y += 8) {
      for (let x = 4; x < 64; x += 8) {
        const lit = rand();
        g.fillStyle = lit > 0.86 ? "#fde68a" : lit > 0.5 ? "#9fb4c8" : "#b9c8d6";
        g.fillRect(x, y, 5, 5);
      }
    }
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    tex.repeat.set(2, 4);
    return tex;
  }, []);
}

const TOWER_TONES = ["#f1f5f9", "#e2e8f0", "#cbd5e1", "#e7e5e4", "#dbeafe", "#f5f5f4", "#d6d3d1", "#bfdbfe"];

/**
 * Manhattan around the park: blocks of towers on all four sides, supertall needles along the south.
 * `omit` drops edges ("north" | "south" | "east" | "west"), e.g. the row the boardroom tower stands in.
 */
export function Skyline({ seed = 5, rows = 3, omit = [] }) {
  const map = useWindowTexture();
  const towers = useMemo(() => {
    const rand = rng(seed);
    const out = [];
    const edge = (axis, sign, name) => {
      if (omit.includes(name)) return;
      const len = axis === "x" ? PARK.halfX : PARK.halfZ;
      const south = axis === "x" && sign < 0; // Central Park South
      for (let r = 0; r < rows; r += 1) {
        for (let u = -len - 8; u <= len + 8; u += 3.6 + rand() * 2) {
          const off = (axis === "x" ? PARK.halfZ : PARK.halfX) + 4 + r * 6 + rand() * 1.5;
          const w = 2.4 + rand() * 2.2;
          const d = 2.4 + rand() * 2.2;
          let h = 5 + rand() * 12 + r * 4;
          if (south && rand() < 0.22) h = 32 + rand() * 26; // supertalls
          else if (rand() < 0.08) h += 14;
          const x = axis === "x" ? u : sign * off;
          const z = axis === "x" ? sign * off : u;
          out.push({ pos: [x, h / 2, z], scale: [w, h, d], color: TOWER_TONES[Math.floor(rand() * TOWER_TONES.length)] });
        }
      }
    };
    edge("x", -1, "south"); edge("x", 1, "north"); edge("z", -1, "west"); edge("z", 1, "east");
    return out;
  }, [seed, rows, omit]);
  const geo = useMemo(() => new THREE.BoxGeometry(1, 1, 1), []);
  const mat = useMemo(() => new THREE.MeshStandardMaterial({ map, roughness: 0.55, metalness: 0.15 }), [map]);
  return <Instanced geometry={geo} material={mat} items={towers} />;
}

/** The full park, for the town and the boardroom view. */
export default function CentralPark({ clearings, paths, trees = 260, omitSkyline }) {
  return (
    <group>
      <Grounds paths={paths} />
      <Lake />
      <Trees count={trees} clearings={clearings} />
      <Skyline omit={omitSkyline} />
    </group>
  );
}

/** Painted window view of the park and skyline, for office windows (cheap: one canvas texture). */
export function useParkViewTexture(seed = 1) {
  return useMemo(() => {
    const W = 512, H = 384;
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    const g = c.getContext("2d");
    const sky = g.createLinearGradient(0, 0, 0, H * 0.6);
    sky.addColorStop(0, "#bfe3fb");
    sky.addColorStop(1, "#eef7fd");
    g.fillStyle = sky;
    g.fillRect(0, 0, W, H);
    const rand = rng(seed);
    // far skyline, then nearer towers with windows
    for (const [base, tone, tall] of [[0.52, "#d6e0ea", 0.25], [0.6, "#c3d0dd", 0.4]]) {
      for (let x = -10; x < W; x += 14 + rand() * 26) {
        const w = 16 + rand() * 30;
        const h = H * (0.08 + rand() * tall);
        g.fillStyle = tone;
        g.fillRect(x, H * base - h, w, h + 4);
        g.fillStyle = "rgba(255,255,255,0.55)";
        for (let wy = H * base - h + 5; wy < H * base - 4; wy += 7) for (let wx = x + 3; wx < x + w - 3; wx += 6) if (rand() > 0.35) g.fillRect(wx, wy, 3, 3);
      }
    }
    // park canopy and lawn
    g.fillStyle = "#a3d977";
    g.fillRect(0, H * 0.6, W, H * 0.4);
    for (let i = 0; i < 70; i += 1) {
      const x = rand() * W;
      const y = H * (0.6 + rand() * 0.25);
      const r = 10 + rand() * 22;
      g.fillStyle = ["#65a30d", "#4d7c0f", "#84cc16", "#16a34a"][Math.floor(rand() * 4)];
      g.beginPath();
      g.arc(x, y, r, 0, Math.PI * 2);
      g.fill();
    }
    g.fillStyle = "#7cc4e8"; // a glimpse of the lake
    g.beginPath();
    g.ellipse(W * 0.3, H * 0.9, W * 0.22, H * 0.05, 0, 0, Math.PI * 2);
    g.fill();
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }, [seed]);
}
