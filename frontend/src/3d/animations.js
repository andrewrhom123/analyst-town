import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useRef } from "react";
import * as THREE from "three";

export const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/**
 * Camera rig: when `flyTo` is set ({ position: [x,y,z], lookAt: [x,y,z] }), eases the camera there over
 * `duration` seconds and then calls onArrive (used to "walk into" a house before routing to the office).
 */
export function CameraRig({ flyTo, duration = 0.9, onArrive, controlsRef }) {
  const { camera } = useThree();
  const anim = useRef(null);

  useEffect(() => {
    if (!flyTo) return;
    const target = controlsRef?.current?.target?.clone() ?? new THREE.Vector3();
    anim.current = {
      t: 0,
      fromPos: camera.position.clone(),
      toPos: new THREE.Vector3(...flyTo.position),
      fromLook: target,
      toLook: new THREE.Vector3(...flyTo.lookAt),
    };
    if (controlsRef?.current) controlsRef.current.enabled = false;
  }, [flyTo, camera, controlsRef]);

  useFrame((_, delta) => {
    const a = anim.current;
    if (!a) return;
    a.t = Math.min(1, a.t + delta / duration);
    const k = easeInOut(a.t);
    camera.position.lerpVectors(a.fromPos, a.toPos, k);
    const look = new THREE.Vector3().lerpVectors(a.fromLook, a.toLook, k);
    camera.lookAt(look);
    if (controlsRef?.current) controlsRef.current.target.copy(look);
    if (a.t >= 1) {
      anim.current = null;
      onArrive?.();
    }
  });
  return null;
}

/** Gentle idle bob for robots; faster when working. */
export function useBob(ref, { speed = 1.6, height = 0.06, phase = 0 } = {}) {
  useFrame(({ clock }) => {
    if (ref.current) ref.current.position.y = Math.sin(clock.elapsedTime * speed + phase) * height;
  });
}
