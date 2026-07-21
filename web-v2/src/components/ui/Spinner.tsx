/**
 * The one spinner — single animation, two sizes.
 * sm (13px) for inline/button contexts, md (22px) for panel loads.
 */
export function Spinner({ size = "sm", label = "Loading" }: { size?: "sm" | "md"; label?: string }) {
  return <span className={`ds-spinner ds-spinner--${size}`} role="status" aria-label={label} />;
}
