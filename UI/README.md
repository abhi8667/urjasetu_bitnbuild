# UrjaSetu City Grid Live

An interactive 3D city-scale power transmission simulator built with React,
Three.js, and Vite. The included deterministic sample dataset models buildings,
generators, a 220 kV grid station, 66 kV substations, 11 kV transformers, and
their transmission lines.

## Local development

```bash
npm install
npm run dev
```

## Production build

```bash
npm run build
```

The project is ready to deploy to Vercel with `UI` selected as the root
directory. Vercel can use the default Vite build settings (`npm run build` and
`dist`).

## Navigation

- Left-drag to orbit
- Right-drag to pan
- Scroll to change altitude
- WASD or arrow keys to fly across the map
- Select any building or grid asset to inspect it
