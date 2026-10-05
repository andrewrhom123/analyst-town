import { Html } from "@react-three/drei";
import { createContext, useContext, useRef } from "react";

/*
 * drei's <Html> re-creates its DOM root when its default target (the canvas's parent node) changes right
 * after mount, which leaves the first label in a scene empty ("Attempted to synchronously unmount a root").
 * Scenes wrap their <Canvas> in <LabelLayer>, and <Label> portals into that stable element instead.
 */
const LabelPortal = createContext(null);

/** Positioned wrapper for a <Canvas>; labels inside the scene render into it. */
export function LabelLayer({ className, style, children }) {
  const ref = useRef(null);
  return (
    <div ref={ref} className={className} style={{ position: "absolute", inset: 0, ...style }}>
      <LabelPortal.Provider value={ref}>{children}</LabelPortal.Provider>
    </div>
  );
}

/** drei <Html> that renders into the nearest LabelLayer. */
export function Label(props) {
  const portal = useContext(LabelPortal);
  return <Html portal={portal || undefined} {...props} />;
}
