import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import { useBob } from "./animations.js";

/** Low-poly robot: body, head, glowing visor and a status antenna (blinks faster while working). */
export default function Robot({ color = "#22d3ee", working = false, phase = 0, scale = 1, seated = false, ...props }) {
  const bob = useRef();
  const antenna = useRef();
  const visor = useRef();
  useBob(bob, { speed: working ? 3.2 : 1.6, height: seated ? 0.02 : 0.05, phase });

  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    if (antenna.current) antenna.current.emissiveIntensity = working ? 1.2 + Math.sin(t * 10) * 1.0 : 0.8 + Math.sin(t * 2 + phase) * 0.3;
    if (visor.current) visor.current.emissiveIntensity = 1.4 + Math.sin(t * 0.8 + phase) * 0.2;
  });

  return (
    <group scale={scale} {...props}>
      <group ref={bob}>
        {/* legs (hidden when seated) */}
        {!seated && (
          <>
            <mesh position={[-0.13, 0.22, 0]} castShadow>
              <boxGeometry args={[0.12, 0.44, 0.14]} />
              <meshStandardMaterial color="#2a3550" metalness={0.6} roughness={0.4} />
            </mesh>
            <mesh position={[0.13, 0.22, 0]} castShadow>
              <boxGeometry args={[0.12, 0.44, 0.14]} />
              <meshStandardMaterial color="#2a3550" metalness={0.6} roughness={0.4} />
            </mesh>
          </>
        )}
        {/* body */}
        <mesh position={[0, 0.72, 0]} castShadow>
          <boxGeometry args={[0.5, 0.56, 0.32]} />
          <meshStandardMaterial color="#c7d2e6" metalness={0.5} roughness={0.35} />
        </mesh>
        {/* chest light */}
        <mesh position={[0, 0.78, 0.165]}>
          <circleGeometry args={[0.07, 24]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.4} />
        </mesh>
        {/* arms */}
        <mesh position={[-0.32, 0.72, 0]} rotation={[seated ? -1.1 : 0, 0, 0.12]} castShadow>
          <boxGeometry args={[0.1, 0.44, 0.12]} />
          <meshStandardMaterial color="#9aa8c2" metalness={0.6} roughness={0.4} />
        </mesh>
        <mesh position={[0.32, 0.72, 0]} rotation={[seated ? -1.1 : 0, 0, -0.12]} castShadow>
          <boxGeometry args={[0.1, 0.44, 0.12]} />
          <meshStandardMaterial color="#9aa8c2" metalness={0.6} roughness={0.4} />
        </mesh>
        {/* head */}
        <mesh position={[0, 1.18, 0]} castShadow>
          <boxGeometry args={[0.42, 0.34, 0.34]} />
          <meshStandardMaterial color="#dfe7f5" metalness={0.4} roughness={0.3} />
        </mesh>
        {/* visor */}
        <mesh position={[0, 1.19, 0.172]}>
          <planeGeometry args={[0.32, 0.12]} />
          <meshStandardMaterial ref={visor} color={color} emissive={color} emissiveIntensity={1.5} />
        </mesh>
        {/* antenna */}
        <mesh position={[0, 1.42, 0]}>
          <cylinderGeometry args={[0.015, 0.015, 0.16, 8]} />
          <meshStandardMaterial color="#9aa8c2" />
        </mesh>
        <mesh position={[0, 1.52, 0]}>
          <sphereGeometry args={[0.045, 16, 16]} />
          <meshStandardMaterial ref={antenna} color={working ? "#4ade80" : color} emissive={working ? "#4ade80" : color} emissiveIntensity={1} />
        </mesh>
      </group>
    </group>
  );
}
