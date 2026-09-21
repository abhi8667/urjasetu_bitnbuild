import type { ReactNode } from 'react'

export type MascotEmote = 'waving' | 'pointing' | 'thinking' | 'energy' | 'celebrating'

interface UrjaMascotProps {
  emote?: MascotEmote
  className?: string
  size?: number
}

export function UrjaMascot({ emote = 'waving', className = '', size = 48 }: UrjaMascotProps) {
  return (
    <div
      className={`urjabot-avatar emote-${emote} ${className}`}
      aria-hidden="true"
      style={{ width: size, height: size, minWidth: size, minHeight: size }}
    >
      <svg
        className="urjabot-svg"
        viewBox="0 0 90 90"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ width: '100%', height: '100%' }}
      >
        <defs>
          <linearGradient id="chassisGrad" x1="20" y1="15" x2="70" y2="85" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor="#1e293b" />
            <stop offset="60%" stopColor="#0f172a" />
            <stop offset="100%" stopColor="#090d16" />
          </linearGradient>

          <linearGradient id="visorGrad" x1="25" y1="28" x2="65" y2="52" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor="#0284c7" stopOpacity="0.3" />
            <stop offset="100%" stopColor="#0369a1" stopOpacity="0.6" />
          </linearGradient>

          <linearGradient id="reactorGrad" x1="45" y1="60" x2="45" y2="70" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor="#38bdf8" />
            <stop offset="100%" stopColor="#0284c7" />
          </linearGradient>

          <filter id="softGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        </defs>

        {/* Ambient Ground Glow */}
        <ellipse cx="45" cy="84" rx="22" ry="3.5" fill="#38bdf8" fillOpacity="0.18" filter="url(#softGlow)" />

        {/* Antenna with Electric Node */}
        <line x1="45" y1="18" x2="45" y2="9" stroke="#64748b" strokeWidth="2.5" strokeLinecap="round" />
        <circle cx="45" cy="8" r="3.5" fill="#38bdf8" filter="url(#softGlow)" />

        {/* Lower Body Chassis */}
        <rect
          x="24"
          y="52"
          width="42"
          height="22"
          rx="10"
          fill="url(#chassisGrad)"
          stroke="rgba(56, 189, 248, 0.28)"
          strokeWidth="1.2"
        />

        {/* Chest Microgrid Arc Reactor */}
        <circle cx="45" cy="63" r="5" fill="url(#reactorGrad)" filter="url(#softGlow)" />
        <polygon points="45,60 48,64.5 42,64.5" fill="#f8fafc" opacity="0.95" />

        {/* Head Shell */}
        <rect
          x="20"
          y="18"
          width="50"
          height="32"
          rx="13"
          fill="url(#chassisGrad)"
          stroke="rgba(56, 189, 248, 0.35)"
          strokeWidth="1.2"
        />

        {/* Visor Screen */}
        <rect
          x="25"
          y="23"
          width="40"
          height="22"
          rx="8"
          fill="#050b14"
          stroke="rgba(56, 189, 248, 0.2)"
          strokeWidth="1"
        />
        <rect x="26" y="24" width="38" height="20" rx="7" fill="url(#visorGrad)" />

        {/* Expressive LED Eyes */}
        {emote === 'thinking' ? (
          <>
            <circle cx="36" cy="35" r="3.2" fill="#38bdf8" filter="url(#softGlow)" />
            <path d="M 50 33 Q 56 31 60 36" stroke="#38bdf8" strokeWidth="2.4" strokeLinecap="round" fill="none" filter="url(#softGlow)" />
          </>
        ) : emote === 'celebrating' ? (
          <>
            <path d="M 31 36 Q 36 30 41 36" stroke="#38bdf8" strokeWidth="2.4" strokeLinecap="round" fill="none" filter="url(#softGlow)" />
            <path d="M 49 36 Q 54 30 59 36" stroke="#38bdf8" strokeWidth="2.4" strokeLinecap="round" fill="none" filter="url(#softGlow)" />
          </>
        ) : (
          <>
            <circle cx="36" cy="35" r="3.2" fill="#38bdf8" filter="url(#softGlow)" />
            <circle cx="54" cy="35" r="3.2" fill="#38bdf8" filter="url(#softGlow)" />
          </>
        )}

        {/* LED Waveform Mouth */}
        {emote === 'celebrating' || emote === 'waving' ? (
          <path d="M 41 40 Q 45 43.5 49 40" stroke="#fbbf24" strokeWidth="1.6" strokeLinecap="round" fill="none" />
        ) : (
          <line x1="42" y1="40" x2="48" y2="40" stroke="#38bdf8" strokeWidth="1.4" strokeLinecap="round" opacity="0.8" />
        )}

        {/* Gesture Arm Indicators */}
        {emote === 'waving' && (
          <>
            <path d="M 24 55 Q 16 61 18 66" stroke="#64748b" strokeWidth="2.8" strokeLinecap="round" />
            <path d="M 66 55 Q 76 47 74 38" stroke="#38bdf8" strokeWidth="3" strokeLinecap="round" filter="url(#softGlow)" />
            <circle cx="74" cy="37" r="2.5" fill="#fbbf24" />
          </>
        )}

        {emote === 'pointing' && (
          <>
            <path d="M 24 55 Q 18 61 19 66" stroke="#64748b" strokeWidth="2.8" strokeLinecap="round" />
            <path d="M 66 55 Q 77 49 81 40" stroke="#38bdf8" strokeWidth="3" strokeLinecap="round" filter="url(#softGlow)" />
            <polygon points="81,37 83,42 78,41" fill="#38bdf8" />
          </>
        )}

        {emote === 'thinking' && (
          <>
            <path d="M 24 55 Q 18 62 20 67" stroke="#64748b" strokeWidth="2.8" strokeLinecap="round" />
            <path d="M 66 57 Q 64 48 55 46" stroke="#38bdf8" strokeWidth="2.8" strokeLinecap="round" />
          </>
        )}

        {emote === 'celebrating' && (
          <>
            <path d="M 24 55 Q 15 44 17 35" stroke="#38bdf8" strokeWidth="3" strokeLinecap="round" />
            <path d="M 66 55 Q 75 44 73 35" stroke="#38bdf8" strokeWidth="3" strokeLinecap="round" />
            <circle cx="16" cy="31" r="2" fill="#fbbf24" />
            <circle cx="74" cy="31" r="2" fill="#fbbf24" />
          </>
        )}

        {emote === 'energy' && (
          <>
            <path d="M 24 55 Q 18 57 17 63" stroke="#38bdf8" strokeWidth="2.8" strokeLinecap="round" />
            <path d="M 66 55 Q 72 57 73 63" stroke="#38bdf8" strokeWidth="2.8" strokeLinecap="round" />
            <circle cx="45" cy="63" r="9" stroke="#38bdf8" strokeWidth="1" strokeDasharray="3 2" opacity="0.6" />
          </>
        )}
      </svg>
    </div>
  )
}

