"""The bridge between the engine and the browser.

`engine/` and `grid/` know nothing about HTTP, and that stays true: this package
reads the engine through the two one-way channels the engine already defines —
the bus ring buffer and `Runner.on_block` — and translates what it finds into
the payload shapes `UI/src/types.ts` declares. Nothing here writes engine state.
"""
