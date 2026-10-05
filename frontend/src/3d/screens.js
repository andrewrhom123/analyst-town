import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";

/** Small deterministic PRNG so every screen gets its own (stable) look. */
export function rng(seed) {
  let s = (Math.abs(Math.floor(seed * 9973)) % 2147483646) + 1;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}

export function seedOf(text = "") {
  let h = 7;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) % 100003;
  return h;
}

/**
 * A live "trading screen" texture: scrolling line chart, volume bars and a ticking price readout.
 * Redrawn a few times a second (faster while the agent is working). `variant` picks the layout.
 */
export function useScreenTexture({ color = "#06b6d4", seed = 1, label = "", working = false, variant = 0, width = 256, height = 160 }) {
  const state = useMemo(() => {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
    const rand = rng(seed + variant * 17);
    const points = Array.from({ length: 48 }, () => 0);
    let v = 50;
    for (let i = 0; i < points.length; i++) points[i] = v = Math.max(10, Math.min(90, v + (rand() - 0.48) * 9));
    return { canvas, ctx: canvas.getContext("2d"), texture, rand, points, price: 40 + rand() * 160, acc: 0 };
  }, [seed, variant, width, height]);

  useEffect(() => () => state.texture.dispose(), [state]);

  const draw = () => {
    const { ctx, points, rand } = state;
    const w = width, h = height;
    const last = points[points.length - 1];
    points.shift();
    points.push(Math.max(10, Math.min(90, last + (rand() - 0.48) * 8)));
    state.price *= 1 + (rand() - 0.49) * 0.004;
    const up = points[points.length - 1] >= points[0];

    ctx.fillStyle = "#f8fafc";
    ctx.fillRect(0, 0, w, h);
    // header bar
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, 26);
    ctx.fillStyle = color;
    ctx.fillRect(0, 24, w, 2);
    ctx.fillStyle = "#1a1a1a";
    ctx.font = "bold 15px Inter, Arial, sans-serif";
    ctx.fillText(label || "MARKET", 8, 18);
    ctx.font = "bold 14px 'Roboto Mono', monospace";
    ctx.fillStyle = up ? "#059669" : "#dc2626";
    const px = state.price.toFixed(2);
    ctx.fillText(px, w - 8 - ctx.measureText(px).width, 18);

    // grid
    ctx.strokeStyle = "#e5e7eb";
    ctx.lineWidth = 1;
    for (let y = 40; y < h - 30; y += 22) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }

    const chartTop = 32, chartH = variant === 1 ? h - 46 : h - 74;
    const x = (i) => (i / (points.length - 1)) * w;
    const y = (p) => chartTop + chartH - (p / 100) * chartH;
    // area + line
    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p)) : ctx.moveTo(x(i), y(p))));
    ctx.lineTo(w, chartTop + chartH);
    ctx.lineTo(0, chartTop + chartH);
    ctx.closePath();
    ctx.fillStyle = up ? "rgba(5,150,105,0.12)" : "rgba(220,38,38,0.10)";
    ctx.fill();
    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p)) : ctx.moveTo(x(i), y(p))));
    ctx.strokeStyle = up ? "#059669" : "#dc2626";
    ctx.lineWidth = 2.5;
    ctx.stroke();

    if (variant !== 1) {
      // volume bars in the agent color
      ctx.fillStyle = color;
      for (let i = 0; i < 24; i++) {
        const bh = 6 + rand() * 30;
        ctx.globalAlpha = 0.35 + rand() * 0.5;
        ctx.fillRect(i * (w / 24) + 2, h - bh - 4, w / 24 - 4, bh);
      }
      ctx.globalAlpha = 1;
    }
    state.texture.needsUpdate = true;
  };

  const drawn = useRef(false);
  if (!drawn.current) { draw(); drawn.current = true; }

  useFrame((_, delta) => {
    state.acc += delta;
    if (state.acc > (working ? 0.18 : 0.6)) {
      state.acc = 0;
      draw();
    }
  });
  return state.texture;
}
