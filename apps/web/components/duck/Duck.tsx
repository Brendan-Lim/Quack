import type { DuckState } from "@/lib/types";

// An original duck, drawn as SVG so it stays crisp at any size. CSS in globals.css animates
// it, keyed on `data-state`.
export function Duck({ state, size = 96 }: { state: DuckState; size?: number }) {
  return (
    <svg
      viewBox="0 0 120 120"
      width={size}
      height={size}
      data-state={state}
      data-testid="duck-sprite"
      className="duck"
      aria-hidden
    >
      <ellipse className="duck-shadow" cx="58" cy="112" rx="34" ry="4" fill="#262626" opacity="0.08" />
      <g className="duck-all">
        <g fill="#F4A340">
          <path d="M42 100 q-2 8 -8 10 q6 3 15 0 q-2 -5 -1 -10z" />
          <path d="M66 100 q-2 8 -8 10 q6 3 15 0 q-2 -5 -1 -10z" />
        </g>
        <g className="duck-body">
          <path
            d="M15 58 C22 66 30 66 37 62 C50 53 72 54 84 66 C96 79 90 104 60 105 C32 106 18 92 19 76 C19.4 69 17 64 15 58Z"
            fill="#FFD95A"
          />
          <path
            d="M24 83 C30 97 46 104 60 105 C76 104.500 87 96 88 83 C80 94 66 98 52 96 C40 94 30 90 24 83Z"
            fill="#F7C53E"
            opacity="0.55"
          />
          <path className="duck-wing" d="M34 73 C42 64 58 65 64 74 C60 86 46 90 38 85 C34 82 32 77 34 73Z" fill="#F9CD45" />
        </g>
        <g className="duck-head">
          <circle cx="72" cy="42" r="25" fill="#FFD95A" />
          <path d="M56 28 Q60 18 68 20 Q63 24 62 30Z" fill="#FFD95A" />
          <ellipse cx="66" cy="52" rx="6" ry="3.600" fill="#F4A340" opacity="0.28" />
          <g className="duck-beak">
            <path className="duck-beak-lower" d="M92 47 Q102 49 104 52 Q99 57 90 54Z" fill="#E8913A" />
            <path d="M92 39 Q104 40 108 46 Q102 52 91 50 Q88 45 92 39Z" fill="#F4A340" />
          </g>
          <g className="duck-eyes">
            <g className="duck-eye-open">
              <circle cx="80" cy="36" r="5" fill="#262626" />
              <circle cx="81.800" cy="34" r="1.700" fill="#FFFFFF" />
            </g>
            <path className="duck-eye-happy" d="M74.500 37 Q80 30 85.500 37" stroke="#262626" strokeWidth="3" strokeLinecap="round" fill="none" />
          </g>
        </g>
      </g>
    </svg>
  );
}
