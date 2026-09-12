/// <reference types="vite/client" />

// Typed so `import.meta.env.VITE_*` resolves. Without this `tsc -b` rejects
// every read in src/config.ts with "Property 'env' does not exist on ImportMeta".
interface ImportMetaEnv {
  readonly VITE_ENGINE_URL?: string
  readonly VITE_STREAM_CADENCE?: string
  readonly VITE_STREAM_DAYS?: string
  readonly VITE_STREAM_START?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
