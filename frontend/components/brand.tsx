export function Brand({ subtitle }: { subtitle: string }) {
  return (
    <div className="brand brand-signature" aria-label={`NovaCart — ${subtitle}`}>
      <svg className="brand-mark" width="42" height="42" viewBox="0 0 48 48" fill="none" aria-hidden="true">
        <path d="M5 39V9a4 4 0 0 1 4-4h6v34H5Z" fill="#243F52" />
        <path d="M15 5h1.5L33 29V5h10v34a4 4 0 0 1-4 4h-6L15 17V5Z" fill="#243F52" />
        <path d="M15 5h1.5L33 29v14L15 17V5Z" fill="#638E87" />
      </svg>
      <div><strong className="brand-wordmark">Nova<span>Cart</span></strong><small>{subtitle}</small></div>
    </div>
  );
}
