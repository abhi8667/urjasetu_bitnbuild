import { useCallback, useLayoutEffect, useRef } from 'react'

const eventIds = new WeakMap<object, number>()
let nextEventId = 1

/** Keep streamed rows mounted as the surrounding event window changes. */
export function activityEventKey(event: object) {
  let id = eventIds.get(event)
  if (id == null) {
    id = nextEventId++
    eventIds.set(event, id)
  }
  return id
}

/**
 * Follow the live edge until the reader scrolls away or opens an expandable
 * entry. The follow decision is recorded by scroll events, before React adds
 * the next row, rather than inferred from the already-resized scroll area.
 */
export function useActivityScroll(updateSignal: unknown, liveEdge: 'top' | 'bottom') {
  const ref = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const programmaticScroll = useRef(false)
  const scrollEndTimer = useRef<number | undefined>(undefined)

  const distanceFromLiveEdge = useCallback((element: HTMLDivElement) => (
    liveEdge === 'top'
      ? element.scrollTop
      : element.scrollHeight - element.scrollTop - element.clientHeight
  ), [liveEdge])

  const syncFollowing = useCallback(() => {
    const element = ref.current
    if (element) following.current = distanceFromLiveEdge(element) <= 2
  }, [distanceFromLiveEdge])

  const onScroll = useCallback(() => {
    if (!programmaticScroll.current) syncFollowing()
  }, [syncFollowing])

  const onUserScroll = useCallback(() => {
    programmaticScroll.current = false
    window.clearTimeout(scrollEndTimer.current)
    // Wheel fires before the browser changes scrollTop. Read it on the next
    // frame so a wheel gesture at the bottom keeps live following enabled.
    window.requestAnimationFrame(syncFollowing)
  }, [syncFollowing])

  const syncAfterLayout = useCallback(() => {
    window.requestAnimationFrame(syncFollowing)
  }, [syncFollowing])

  useLayoutEffect(() => {
    const element = ref.current
    if (!element || !following.current) return
    programmaticScroll.current = true
    window.clearTimeout(scrollEndTimer.current)
    element.scrollTo({
      top: liveEdge === 'top' ? 0 : element.scrollHeight,
      behavior: 'smooth',
    })
    scrollEndTimer.current = window.setTimeout(() => {
      programmaticScroll.current = false
      syncFollowing()
    }, 350)

    return () => window.clearTimeout(scrollEndTimer.current)
  }, [liveEdge, syncFollowing, updateSignal])

  return { ref, onScroll, onUserScroll, syncAfterLayout }
}
