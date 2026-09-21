import { useCallback, useEffect } from 'react'

import { UrjaMascot, type MascotEmote } from './UrjaMascot'
import { SplashScreen } from './SplashScreen'
import type { CameraMode } from './City3D'

export type TourDockPlacement =
  | 'bottom-center'
  | 'top-under-hud'
  | 'bottom-right'
  | 'bottom-left'
  | 'left-middle'
  | 'top-center'

export interface TourSceneConfig {
  step: number
  title: string
  mascotEmote: MascotEmote
  body: string
  callouts: string[]
  spotlightSelector?: string
  cameraMode?: CameraMode
  detailPanel?: 'health' | 'ledger' | 'node' | 'governance' | 'agents' | null
  showTelemetryGraph?: boolean
  scenario?: 'battery-custody' | 'reset'
  placement: TourDockPlacement
}

const TOUR_SCENES: Record<number, TourSceneConfig> = {
  1: {
    step: 1,
    title: 'Urban Microgrid Architecture',
    mascotEmote: 'waving',
    body: 'Welcome to UrjaSetu! Here is our 64-node urban distribution grid connected across 4 localized distribution transformers (DT-1 to DT-4). Notice the mixture of residential consumers, rooftop solar prosumers, and neighborhood battery storage systems.',
    callouts: [
      '64 premises across 4 neighborhood distribution transformers',
      'Autonomous localized dispatch minimizes distribution line losses',
    ],
    cameraMode: 'orbit',
    detailPanel: null,
    showTelemetryGraph: false,
    placement: 'left-middle',
  },
  2: {
    step: 2,
    title: 'Equilibrium Market Clearing',
    mascotEmote: 'pointing',
    body: 'When rooftop solar peaks, prosumers automatically sell surplus power locally. Our continuous double auction computes an equilibrium clearing price that gives buyers up to 25% savings over DISCOM tariffs while maximizing seller revenue.',
    callouts: [
      'Local double auction settles every hourly block',
      'Guaranteed discount vs DISCOM retail slab tariffs',
    ],
    spotlightSelector: '.hud-top-left',
    cameraMode: 'perspective',
    detailPanel: null,
    showTelemetryGraph: false,
    placement: 'top-under-hud',
  },
  3: {
    step: 3,
    title: '3D Agent Swarm & Autonomous Stream',
    mascotEmote: 'thinking',
    body: 'Every microgrid actor is represented by an autonomous software agent. Behold our 3D Agent Network: watch live packet flows linking Grid Risk ML forecasting, Trading Strategy LLMs, and Market Cleared dispatches in real-time!',
    callouts: [
      '13 specialized agent roles across 5 functional families (ML, LLM, Logic, System, Governance)',
      'Dynamic message packets trace inter-agent consensus in 3D',
      'Live stream logs reasoning, bidding constraints, and thermal adders',
    ],
    detailPanel: 'agents',
    showTelemetryGraph: false,
    placement: 'top-center',
  },
  4: {
    step: 4,
    title: 'P2P Battery Custody Storage',
    mascotEmote: 'energy',
    body: 'A critical innovation: When a prosumer\'s battery reaches 100%, surplus generation would normally be curtailed. UrjaSetu\'s Flow Agent automatically routes excess power into a neighbor\'s battery custody, preserving clean energy on the local feeder!',
    callouts: [
      'Zero solar curtailment through neighbor custody storage',
      'Dynamic energy routing keeps electrons within the cluster',
    ],
    scenario: 'battery-custody',
    detailPanel: 'node',
    showTelemetryGraph: false,
    placement: 'bottom-left',
  },
  5: {
    step: 5,
    title: '24-Hour Real-Time Telemetry Dynamics',
    mascotEmote: 'pointing',
    body: 'Judges can inspect real-time 24-hour supply, demand, and transformer load dynamics. The Sentinel agent identifies impending bottlenecks one block in advance, enabling predictive load shaping.',
    callouts: [
      'Real-time load curve tracking across all 4 transformers',
      'One-block-ahead predictive breach detection',
    ],
    showTelemetryGraph: true,
    detailPanel: null,
    placement: 'bottom-right',
  },
  6: {
    step: 6,
    title: 'Physical Grid Protection & Thermal Ageing',
    mascotEmote: 'pointing',
    body: 'Most P2P platforms ignore physical hardware. UrjaSetu integrates IEEE C57.91 thermal models. When transformer temperatures rise, our market engine applies dynamic ageing adders to throttle stress and prevent physical burnout!',
    callouts: [
      'IEEE C57.91 hot-spot temperature calculations',
      'Dynamic ageing adders signal physical transformer wear',
    ],
    detailPanel: 'health',
    showTelemetryGraph: false,
    placement: 'bottom-left',
  },
  7: {
    step: 7,
    title: 'DISCOM Economics & Deferred Capex',
    mascotEmote: 'pointing',
    body: 'Why do distribution utilities embrace UrjaSetu? DISCOMs earn wheeling charges on every trade while deferring crores in transformer replacements. In fact, standard net-metering degrades assets faster than our managed P2P network!',
    callouts: [
      'DISCOM collects wheeling, platform & GST fees',
      'Deferred transformer capex extends equipment lifespan',
    ],
    detailPanel: 'ledger',
    showTelemetryGraph: false,
    placement: 'bottom-left',
  },
  8: {
    step: 8,
    title: 'Regulatory Compliance & Audit Trails',
    mascotEmote: 'thinking',
    body: 'Engineered from day one for Indian regulatory frameworks: complies with state peer-to-peer sandbox guidelines, maintaining automated cryptographic audit trails and fair-access rules.',
    callouts: [
      'SERC/CERC P2P regulatory sandbox alignment',
      'Transparent cryptographic settlement & dispute safeguards',
    ],
    detailPanel: 'governance',
    showTelemetryGraph: false,
    placement: 'bottom-right',
  },
  9: {
    step: 9,
    title: 'UrjaSetu: People · Power · Together',
    mascotEmote: 'celebrating',
    body: 'Decentralized, resilient, and utility-friendly. The guided presentation is complete! The simulation is now unlocked for live exploration, scenario testing (derating / clouds), or judge Q&A.',
    callouts: [
      'Click "Enter Free Cockpit" to test live scenarios & derates',
      'Press "Replay Tour" to run the presentation pitch again',
    ],
    cameraMode: 'orbit',
    detailPanel: null,
    showTelemetryGraph: false,
    placement: 'bottom-center',
  },
}

interface CinematicTourProps {
  stepIndex: number
  onStepChange: (nextStep: number) => void
  onExitTour: () => void
  setCameraMode: (mode: CameraMode) => void
  setDetailPanel: (panel: 'health' | 'ledger' | 'node' | 'governance' | 'agents' | null) => void
  setShowTelemetryGraph: (show: boolean) => void
  triggerCustodyScenario: () => void
  resetScenarios: () => void
}

export function CinematicTour({
  stepIndex,
  onStepChange,
  onExitTour,
  setCameraMode,
  setDetailPanel,
  setShowTelemetryGraph,
  triggerCustodyScenario,
  resetScenarios,
}: CinematicTourProps) {
  // Apply scene orchestration whenever stepIndex changes
  const applyScene = useCallback(
    (step: number) => {
      const scene = TOUR_SCENES[step]
      if (!scene) return

      if (scene.cameraMode) setCameraMode(scene.cameraMode)
      if (scene.detailPanel !== undefined) setDetailPanel(scene.detailPanel)
      if (scene.showTelemetryGraph !== undefined) setShowTelemetryGraph(scene.showTelemetryGraph)

      if (scene.scenario === 'battery-custody') {
        triggerCustodyScenario()
      } else {
        resetScenarios()
      }
    },
    [setCameraMode, setDetailPanel, setShowTelemetryGraph, triggerCustodyScenario, resetScenarios]
  )

  const goToStep = useCallback(
    (nextStep: number) => {
      onStepChange(nextStep)
      if (nextStep >= 1 && nextStep <= 9) {
        applyScene(nextStep)
      } else if (nextStep === 0 || nextStep === -1) {
        // Reset panels when stepping back into splash or blackout
        setDetailPanel(null)
        setShowTelemetryGraph(false)
        resetScenarios()
      }
    },
    [onStepChange, applyScene, setDetailPanel, setShowTelemetryGraph, resetScenarios]
  )

  const handleNext = useCallback(() => {
    if (stepIndex < 9) {
      goToStep(stepIndex + 1)
    } else {
      onExitTour()
    }
  }, [stepIndex, goToStep, onExitTour])

  const handlePrev = useCallback(() => {
    if (stepIndex > -1) {
      goToStep(stepIndex - 1)
    }
  }, [stepIndex, goToStep])

  // Global Keyboard Listener for big-screen presentation navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't intercept if user is typing in a form field
      const target = e.target
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLSelectElement ||
        target instanceof HTMLTextAreaElement
      ) {
        return
      }

      if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown') {
        e.preventDefault()
        handleNext()
      } else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
        e.preventDefault()
        handlePrev()
      } else if (e.key === 'Escape') {
        e.preventDefault()
        onExitTour()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleNext, handlePrev, onExitTour])

  // Manage spotlight element classes
  useEffect(() => {
    const scene = TOUR_SCENES[stepIndex]
    const selector = scene?.spotlightSelector

    if (selector) {
      const el = document.querySelector(selector)
      el?.classList.add('tour-highlighted-element')
      return () => {
        el?.classList.remove('tour-highlighted-element')
      }
    }
  }, [stepIndex])

  // Scene -1: Cinematic Blackout
  if (stepIndex === -1) {
    return (
      <div
        className="cinematic-blackout-overlay"
        onClick={handleNext}
        role="button"
        tabIndex={0}
        aria-label="Cinematic Blackout - Click or press Right Arrow to begin"
      >
        <div className="blackout-grid-canvas" />

        <div className="blackout-core">
          <div className="blackout-pulse-orb">
            <svg className="blackout-bolt" viewBox="0 0 24 24" fill="currentColor">
              <path d="M13 2L3 14h8l-2 8 10-12h-8l2-8z" />
            </svg>
          </div>

          <h2 className="blackout-title">Initializing UrjaSetu Microgrid</h2>
          <p className="blackout-subtitle">
            Autonomous multi-agent grid dynamics · Physical IEEE C57.91 constraint checks
          </p>

          <div className="blackout-prompt">
            <span>Press</span>
            <span className="blackout-key-badge">→ Right Arrow</span>
            <span>or Click to Enter</span>
          </div>
        </div>
      </div>
    )
  }

  // Scene 0: Hero Splash Screen
  if (stepIndex === 0) {
    return (
      <SplashScreen
        onStartTour={() => goToStep(1)}
        onBackToBlackout={() => goToStep(-1)}
        onSkipToFreeView={onExitTour}
      />
    )
  }

  // Scenes 1 to 9: Live Presentation Dialog & Spotlight
  const activeScene = TOUR_SCENES[stepIndex]
  if (!activeScene) return null

  return (
    <>
      {/* Letterbox Bars for cinematic framing */}
      <div className="cinematic-letterbox-top" />
      <div className="cinematic-letterbox-bottom" />

      {/* Spotlight Backdrop if active feature has spotlight */}
      {activeScene.spotlightSelector && <div className="tour-spotlight-backdrop" />}

      {/* Presentation Dock with Scene-Adaptive Placement */}
      <div
        className={`cinematic-dialog-dock dock-placement-${activeScene.placement}`}
        role="region"
        aria-label="Demo presentation dialogue"
      >
        <div className="cinematic-dialog-bubble">
          <div className="dialog-header-row">
            <div className="dialog-presenter-badge">
              <UrjaMascot emote={activeScene.mascotEmote} size={44} />
              <div className="dialog-presenter-info">
                <span className="dialog-presenter-title">UrjaBot</span>
                <span className="dialog-step-sub">Guide · Scene {activeScene.step}/9</span>
              </div>
            </div>

            <div className="dialog-header-right">
              <div className="dialog-step-pill">
                <span className="dialog-step-dot" />
                <span>Demo Tour</span>
              </div>

              <button
                className="dialog-exit-btn"
                onClick={onExitTour}
                title="Exit Demo Tour to Free Operator Cockpit (Esc)"
              >
                <span>Exit</span>
                <kbd className="dialog-kbd">Esc</kbd>
              </button>
            </div>
          </div>

          <h3 className="dialog-title">{activeScene.title}</h3>
          <p className="dialog-body-text">{activeScene.body}</p>

          <div className="dialog-callout-list">
            {activeScene.callouts.map((callout, i) => (
              <div key={i} className="dialog-callout-item">
                <span className="dialog-callout-bullet">✦</span>
                <span>{callout}</span>
              </div>
            ))}
          </div>

          <div className="dialog-footer-row">
            <div className="dialog-nav-group">
              <button
                className="dialog-prev-btn"
                onClick={handlePrev}
                title="Previous Scene (Left Arrow)"
              >
                ← Back
              </button>

              <button
                className="dialog-next-btn"
                onClick={handleNext}
                autoFocus
                title={stepIndex === 9 ? 'Finish & Explore Freely' : 'Next Scene (Right Arrow or Space)'}
              >
                {stepIndex === 9 ? (
                  <span>Explore Freely ✓</span>
                ) : (
                  <>
                    <span>Next Scene</span>
                    <span>→</span>
                  </>
                )}
              </button>

              {stepIndex === 9 && (
                <button
                  className="dialog-prev-btn"
                  onClick={() => goToStep(0)}
                  title="Replay from Splash Screen"
                >
                  ↺ Replay Tour
                </button>
              )}
            </div>

            <div className="dialog-key-hints">
              <span className="dialog-key-item">
                <kbd className="dialog-kbd">←</kbd> <kbd className="dialog-kbd">→</kbd> or <kbd className="dialog-kbd">Space</kbd>
              </span>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
