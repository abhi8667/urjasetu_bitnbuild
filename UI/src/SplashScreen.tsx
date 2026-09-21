import { UrjaMascot } from './UrjaMascot'

interface SplashScreenProps {
  onStartTour: () => void
  onBackToBlackout: () => void
  onSkipToFreeView: () => void
}

export function SplashScreen({ onStartTour, onBackToBlackout, onSkipToFreeView }: SplashScreenProps) {
  return (
    <div className="cinematic-splash-overlay" role="dialog" aria-modal="true" aria-label="UrjaSetu Presentation Splash">
      <div className="splash-glow-backdrop" />

      <div className="splash-container">
        {/* Top Badges */}
        <div className="splash-badge-row">
          <span className="splash-tag-pill">⚡ Bit n Build 2026</span>
          <span className="splash-hackathon-pill">Autonomous Microgrid System</span>
        </div>

        {/* Brand Header */}
        <div className="splash-logo-container">
          <div className="splash-brand-icon">
            <svg viewBox="0 0 24 24">
              <path d="M13 2L3 14h8l-2 8 10-12h-8l2-8z" />
            </svg>
          </div>
          <h1 className="splash-brand-title">URJASETU</h1>
        </div>

        <div className="splash-tagline">People · Power · Together</div>

        <p className="splash-desc">
          A physical-aware, multi-agent decentralized energy trading network.
          Enabling localized peer-to-peer electricity markets while actively preventing transformer
          stress and solar curtailment using IEEE C57.91 thermal dynamics.
        </p>

        {/* Mascot Introduction */}
        <div className="splash-mascot-wrapper">
          <UrjaMascot emote="waving" />
        </div>

        {/* Action Buttons */}
        <div className="splash-actions">
          <button
            className="splash-back-btn"
            onClick={onBackToBlackout}
            title="Return to blackout (Shortcut: Left Arrow)"
          >
            ← Back
          </button>

          <button
            className="splash-start-btn"
            onClick={onStartTour}
            autoFocus
            title="Begin Presentation Tour (Shortcut: Right Arrow or Space)"
          >
            <span>Begin Pitch Tour</span>
            <span className="blackout-key-badge">→ / Space</span>
          </button>
        </div>

        <button className="splash-skip-link" onClick={onSkipToFreeView}>
          or skip directly to Free Operator Cockpit
        </button>
      </div>
    </div>
  )
}
