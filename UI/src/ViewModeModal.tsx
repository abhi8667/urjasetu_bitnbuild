interface ViewModeModalProps {
  onSelectDemo: () => void
  onSelectFree: () => void
}

export function ViewModeModal({ onSelectDemo, onSelectFree }: ViewModeModalProps) {
  return (
    <div className="view-mode-modal-backdrop" role="dialog" aria-modal="true" aria-label="Select Experience Mode">
      <div className="view-mode-card">
        <div className="view-mode-header">
          <div className="view-mode-pill">URJASETU / EXPERIENCE SELECTOR</div>
          <h2 className="view-mode-title">Welcome to UrjaSetu</h2>
          <p className="view-mode-desc">
            Choose how you would like to explore the decentralized microgrid simulation today.
          </p>
        </div>

        <div className="view-mode-grid">
          {/* Option 1: Demo View (Recommended) */}
          <div
            className="view-mode-option option-demo"
            onClick={onSelectDemo}
            tabIndex={0}
            role="button"
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectDemo() }
            }}
          >
            <span className="option-recommended-tag">Judges &amp; Pitch</span>
            <div className="option-icon-box">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="5 3 19 12 5 21 5 3" fill="currentColor" fillOpacity="0.3" />
              </svg>
            </div>
            <h3 className="option-title">Cinematic Demo Tour</h3>
            <p className="option-text">
              An orchestrated presentation mode. Starts with an electric blackout &amp; hero splash screen,
              then guides the audience through P2P trading, multi-agent reasoning, battery custody, and transformer health with our mascot UrjaBot.
            </p>
            <div className="option-action-row">
              <span>Launch Demo Tour</span>
              <span>→</span>
            </div>
          </div>

          {/* Option 2: Free View */}
          <div
            className="view-mode-option option-free"
            onClick={onSelectFree}
            tabIndex={0}
            role="button"
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectFree() }
            }}
          >
            <div className="option-icon-box">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="14" width="7" height="7" rx="1" />
                <rect x="3" y="14" width="7" height="7" rx="1" />
              </svg>
            </div>
            <h3 className="option-title">Free Operator View</h3>
            <p className="option-text">
              Standard interactive cockpit. Orbit freely in 3D, manually click solar houses, toggle live telemetry graphs,
              inject cloud anomalies, and inspect transformer ageing metrics.
            </p>
            <div className="option-action-row">
              <span>Enter Free View</span>
              <span>→</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
