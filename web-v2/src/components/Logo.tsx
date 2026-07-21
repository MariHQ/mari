/** Blueprint mark — matched to mari.guru / the saas console's Logo. */
export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke="var(--ink)" strokeWidth={1.6} aria-hidden>
      <path d="M4 27 V7 L16 18 L28 7 V27" />
      <rect x="2.2" y="5.2" width="3.6" height="3.6" fill="var(--ink)" />
      <rect x="14.2" y="16.2" width="3.6" height="3.6" fill="var(--biscay)" stroke="var(--biscay)" />
      <rect x="26.2" y="5.2" width="3.6" height="3.6" fill="var(--ink)" />
      <rect x="2.2" y="25.2" width="3.6" height="3.6" fill="var(--ink)" />
      <rect x="26.2" y="25.2" width="3.6" height="3.6" fill="var(--ink)" />
    </svg>
  );
}
