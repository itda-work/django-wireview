import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// One client per mode: `vite build --mode react` builds react/ into dist/react/
export default defineConfig(({ mode }) => ({
  root: mode,
  plugins: mode === "react" ? [react()] : [],
  build: { outDir: `../dist/${mode}`, emptyOutDir: true },
}));
