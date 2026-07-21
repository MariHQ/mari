// Decorative inline SVG: hatched swatches, pencil mountains, small charts.
// No binary assets.

// Global hand-drawn wobble filters; rendered once in App, referenced by id
// from any SVG in the document.
export function SketchDefs() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden>
      <defs>
        <filter id="sketch" x="-8%" y="-8%" width="116%" height="116%">
          <feTurbulence type="fractalNoise" baseFrequency="0.014" numOctaves="3" seed="7" result="n" />
          <feDisplacementMap in="SourceGraphic" in2="n" scale="3.4" xChannelSelector="R" yChannelSelector="G" />
        </filter>
        <filter id="sketch-soft" x="-8%" y="-8%" width="116%" height="116%">
          <feTurbulence type="fractalNoise" baseFrequency="0.02" numOctaves="2" seed="11" result="n" />
          <feDisplacementMap in="SourceGraphic" in2="n" scale="2" xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </defs>
    </svg>
  );
}

export function Hatch({ color, height = 110 }: { color: string; height?: number }) {
  // Dry colored-pencil hatching: layered passes keep the edges irregular
  // without sacrificing the clarity of the stat cards.
  const lines = [];
  for (let i = -6; i < 26; i++) {
    const x = i * 7 + ((i * 13) % 4) - 2;
    const w = 3.4 + ((i * 7) % 3) * 0.5;
    lines.push(
      <line
        key={i}
        x1={x}
        y1={-8}
        x2={x - height * 0.55}
        y2={height + 8}
        stroke={color}
        strokeWidth={w}
        strokeLinecap="round"
        opacity={0.55 + ((i * 11) % 4) * 0.11}
      />
    );
  }
  return (
    <svg width="100%" height="100%" viewBox={`0 0 46 ${height}`} preserveAspectRatio="none" aria-hidden>
      <rect width="46" height={height} fill={color} opacity="0.08" />
      <g filter="url(#sketch-soft)">{lines}</g>
      <g stroke={color} strokeWidth="1.25" strokeLinecap="round" opacity="0.32" filter="url(#sketch-soft)">
        {[...Array(18)].map((_, i) => (
          <line key={i} x1={-18 + i * 8} y1={height + 4} x2={26 + i * 8} y2={-4} />
        ))}
      </g>
      <rect x="1" y="1" width="44" height={height - 2} rx="4" fill="none" stroke={color} strokeWidth="1" opacity="0.22" />
    </svg>
  );
}

const CLOUD_PATH =
  "M20 59 C10 60 4 54 6 45 C7 38 13 33 21 33 C19 24 25 16 34 14 C40 6 54 5 62 12 C75 9 87 17 88 29 C97 31 101 41 96 50 C92 57 85 60 76 59 Z";

export function CloudLogo({ size = 84 }: { size?: number }) {
  // Hand-woven cloud: imperfect repeated marks echo the notebook's crayon lines.
  return (
    <svg width={size} height={size * 0.72} viewBox="0 0 100 72" fill="none" aria-hidden>
      <defs>
        <clipPath id="cloudclip"><path d={CLOUD_PATH} /></clipPath>
        <filter id="cloud-wobble" x="-8%" y="-8%" width="116%" height="116%">
          <feTurbulence type="fractalNoise" baseFrequency="0.032" numOctaves="2" seed="19" result="noise" />
          <feDisplacementMap in="SourceGraphic" in2="noise" scale="0.9" xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </defs>
      <g clipPath="url(#cloudclip)" filter="url(#cloud-wobble)" stroke="#f0f2f5" strokeLinecap="round">
        <path d="M9 24 C29 27 45 32 91 27 M5 33 C30 34 55 41 98 34 M4 42 C34 40 59 49 99 43 M7 51 C31 48 59 57 94 51 M16 59 C39 54 65 62 84 57" strokeWidth="2.15" opacity=".93" />
        <path d="M17 14 C20 28 19 44 16 62 M29 8 C34 24 31 43 28 65 M42 4 C47 23 43 43 42 66 M55 5 C60 23 56 44 55 66 M68 7 C75 24 70 45 68 65 M81 13 C88 29 82 45 80 61 M92 26 C96 38 91 49 88 57" strokeWidth="2" opacity=".86" />
        <path d="M10 29 C34 27 60 38 95 31 M6 47 C33 44 61 54 96 47" strokeWidth=".9" opacity=".5" />
      </g>
      <path d={CLOUD_PATH} stroke="#f0f2f5" strokeWidth="3.1" strokeLinecap="round" strokeLinejoin="round" filter="url(#cloud-wobble)" />
      <path d="M22 62 C38 60 57 64 76 61" stroke="#b23a1e" strokeWidth="1.2" opacity=".38" strokeLinecap="round" />
    </svg>
  );
}

export function Mountains() {
  // faint pencil-sketch mountain range + hatching for the lineage canvas
  return (
    <svg width="100%" height="150" viewBox="0 0 620 150" preserveAspectRatio="none" fill="none" aria-hidden>
      <g stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" filter="url(#sketch)">
        <path d="M-5 148 L 90 66 L 132 100 L 196 34 L 262 112 L 318 70 L 388 128 L 452 58 L 530 120 L 590 84 L 640 148" fill="none" />
        <path d="M60 92 l 18 20 M178 56 l 14 22 M234 92 l 16 18 M430 82 l 16 22 M508 104 l 12 14" opacity="0.6" />
        {[...Array(16)].map((_, i) => (
          <path key={i} d={`M${100 + i * 32} ${120 + (i % 3) * 6} l 14 -10`} opacity="0.35" />
        ))}
      </g>
    </svg>
  );
}

export function Bars({ values, color = "#2c6e49", width = 66, height = 24 }: { values: number[]; color?: string; width?: number; height?: number }) {
  const bw = width / values.length;
  const max = Math.max(...values, 1);
  return (
    <svg width={width} height={height} aria-hidden>
      {values.map((v, i) => {
        const h = Math.max(2, (v / max) * height);
        return <rect key={i} x={i * bw + 1} y={height - h} width={bw - 2} height={h} rx="1" fill={color} opacity={0.5 + (v / max) * 0.5} />;
      })}
    </svg>
  );
}

export function Pulseline({ color = "#6f7c89" }: { color?: string }) {
  return (
    <svg width="30" height="20" viewBox="0 0 30 20" fill="none" aria-hidden>
      <path d="M1 11 h6 l3 -7 4 13 3 -8 2 2 h10" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Ticks({ n = 40 }: { n?: number }) {
  return (
    <svg width="100%" height="100%" viewBox={`0 0 ${n * 10} 34`} preserveAspectRatio="none" aria-hidden className="scrub__ticks">
      <line x1="0" y1="17" x2={n * 10} y2="17" stroke="currentColor" strokeWidth="1.4" />
      {[...Array(n)].map((_, i) => {
        const big = i % 5 === 0;
        return <line key={i} x1={i * 10 + 5} y1={17 - (big ? 6 : 3.5)} x2={i * 10 + 5} y2={17 + (big ? 6 : 3.5)} stroke="currentColor" strokeWidth="1.2" />;
      })}
    </svg>
  );
}

/* ————— texture placeholder for generated imagery: pencil cross-hatch ————— */
const TEX_TONES = ["#b23a1e", "#2c6e49", "#6b7d99", "#a05e1c", "#b23a1e"];

export function TexturePlaceholder({ hue = 30 }: { hue?: number }) {
  const tone = TEX_TONES[hue % TEX_TONES.length];
  return (
    <svg width="100%" height="100%" viewBox="0 0 300 130" preserveAspectRatio="none" aria-hidden>
      <rect width="300" height="130" fill={tone} opacity="0.22" />
      <g stroke={tone} strokeWidth="1.6" strokeLinecap="round" opacity="0.6" filter="url(#sketch-soft)">
        {[...Array(26)].map((_, i) => (
          <line key={`a${i}`} x1={i * 14 - 40} y1={-6} x2={i * 14 + 30 - 40} y2={136} />
        ))}
        {[...Array(14)].map((_, i) => (
          <line key={`b${i}`} x1={i * 26 - ((hue * 7) % 12)} y1={136} x2={i * 26 + 60} y2={-6} opacity="0.45" />
        ))}
      </g>
      <rect width="300" height="130" fill={tone} opacity="0.14" />
    </svg>
  );
}
