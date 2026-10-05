import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import { useBob } from "./animations.js";

const SHELL = { color: "#ffffff", metalness: 0.15, roughness: 0.28 };
const JOINT = { color: "#d7dce3", metalness: 0.5, roughness: 0.35 };
const DARK = { color: "#1f2937", metalness: 0.3, roughness: 0.4 };

/**
 * Glossy white robot with agent-colored accents. `typing` swings the forearms over a keyboard
 * (faster while working) and nods the head at the screen; the antenna blinks while working.
 */
export default function Robot({ color = "#06b6d4", working = false, typing = false, phase = 0, scale = 1, seated = false, ...props }) {
  const bob = useRef();
  const antenna = useRef();
  const visor = useRef();
  const head = useRef();
  const armL = useRef();
  const armR = useRef();
  useBob(bob, { speed: working ? 3 : 1.4, height: seated ? 0.012 : 0.04, phase });

  useFrame(({ clock }) => {
    const t = clock.elapsedTime + phase;
    if (antenna.current) antenna.current.emissiveIntensity = working ? 1.4 + Math.sin(t * 10) * 1.0 : 0.7 + Math.sin(t * 2) * 0.3;
    if (visor.current) visor.current.emissiveIntensity = 1.2 + Math.sin(t * 0.8) * 0.2;
    if (typing) {
      const speed = working ? 16 : 9;
      if (armL.current) armL.current.rotation.x = -1.15 + Math.sin(t * speed) * 0.12;
      if (armR.current) armR.current.rotation.x = -1.15 + Math.sin(t * speed + Math.PI) * 0.12;
      if (head.current) {
        head.current.rotation.x = 0.18 + Math.sin(t * 1.3) * 0.04;
        head.current.rotation.y = Math.sin(t * 0.45) * 0.25; // glances between screens
      }
    }
  });

  const armPose = typing ? -1.15 : seated ? -1.1 : 0;

  return (
    <group scale={scale} {...props}>
      <group ref={bob}>
        {/* legs */}
        {seated ? (
          <>
            {[-0.13, 0.13].map((x) => (
              <group key={x}>
                <mesh position={[x, 0.42, 0.14]} castShadow><boxGeometry args={[0.13, 0.12, 0.34]} /><meshStandardMaterial {...JOINT} /></mesh>
                <mesh position={[x, 0.22, 0.3]} castShadow><boxGeometry args={[0.11, 0.4, 0.12]} /><meshStandardMaterial {...SHELL} /></mesh>
              </group>
            ))}
          </>
        ) : (
          [-0.13, 0.13].map((x) => (
            <group key={x}>
              <mesh position={[x, 0.24, 0]} castShadow><capsuleGeometry args={[0.06, 0.32, 4, 10]} /><meshStandardMaterial {...SHELL} /></mesh>
              <mesh position={[x, 0.03, 0.03]} castShadow><boxGeometry args={[0.14, 0.06, 0.2]} /><meshStandardMaterial {...DARK} /></mesh>
            </group>
          ))
        )}
        {/* torso */}
        <mesh position={[0, 0.74, 0]} castShadow>
          <capsuleGeometry args={[0.22, 0.22, 6, 16]} />
          <meshStandardMaterial {...SHELL} />
        </mesh>
        {/* chest panel + light */}
        <mesh position={[0, 0.76, 0.205]}>
          <boxGeometry args={[0.24, 0.16, 0.02]} />
          <meshStandardMaterial {...DARK} />
        </mesh>
        <mesh position={[0, 0.76, 0.217]}>
          <circleGeometry args={[0.045, 24]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.4} />
        </mesh>
        {/* shoulders + arms (pivot at the shoulder) */}
        {[[-1, armL], [1, armR]].map(([side, ref]) => (
          <group key={side} ref={ref} position={[side * 0.3, 0.9, 0]} rotation={[armPose, 0, side * -0.08]}>
            <mesh castShadow><sphereGeometry args={[0.075, 16, 16]} /><meshStandardMaterial {...JOINT} /></mesh>
            <mesh position={[0, -0.18, 0]} castShadow><capsuleGeometry args={[0.05, 0.24, 4, 10]} /><meshStandardMaterial {...SHELL} /></mesh>
            <mesh position={[0, -0.36, 0]} castShadow><sphereGeometry args={[0.055, 12, 12]} /><meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.25} /></mesh>
          </group>
        ))}
        {/* neck + head */}
        <mesh position={[0, 1.04, 0]}><cylinderGeometry args={[0.06, 0.07, 0.08, 12]} /><meshStandardMaterial {...JOINT} /></mesh>
        <group ref={head} position={[0, 1.22, 0]}>
          <mesh castShadow>
            <boxGeometry args={[0.44, 0.32, 0.34]} />
            <meshStandardMaterial {...SHELL} />
          </mesh>
          {/* face plate + visor */}
          <mesh position={[0, -0.005, 0.172]}>
            <planeGeometry args={[0.38, 0.22]} />
            <meshStandardMaterial {...DARK} />
          </mesh>
          <mesh position={[0, 0.01, 0.174]}>
            <planeGeometry args={[0.3, 0.07]} />
            <meshStandardMaterial ref={visor} color={color} emissive={color} emissiveIntensity={1.3} toneMapped={false} />
          </mesh>
          {/* ear pods */}
          {[-0.235, 0.235].map((x) => (
            <mesh key={x} position={[x, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
              <cylinderGeometry args={[0.06, 0.06, 0.04, 16]} />
              <meshStandardMaterial color={color} metalness={0.3} roughness={0.4} />
            </mesh>
          ))}
          {/* antenna */}
          <mesh position={[0, 0.22, 0]}><cylinderGeometry args={[0.012, 0.012, 0.14, 8]} /><meshStandardMaterial {...JOINT} /></mesh>
          <mesh position={[0, 0.31, 0]}>
            <sphereGeometry args={[0.04, 16, 16]} />
            <meshStandardMaterial ref={antenna} color={working ? "#84cc16" : color} emissive={working ? "#84cc16" : color} emissiveIntensity={1} />
          </mesh>
        </group>
      </group>
    </group>
  );
}
