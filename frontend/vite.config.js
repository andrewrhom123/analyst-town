import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Accept the spec's REACT_APP_API_URL name as well as Vite's own prefix.
  envPrefix: ["VITE_", "REACT_APP_"],
  // three.js is split out automatically by the lazy imports of the 3D scenes and only fetched on
  // laptop-size screens (phones get the card carousel).
  build: { chunkSizeWarningLimit: 1500 },
});
