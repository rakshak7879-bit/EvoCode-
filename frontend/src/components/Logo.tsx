import { cn } from '../lib/format'

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={cn('size-8', className)} aria-hidden>
      <defs>
        <linearGradient id="evo-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#a78bfa" />
          <stop offset="1" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill="#0d121b" stroke="rgba(255,255,255,0.08)" />
      <path d="M32 10 51 21v22L32 54 13 43V21z" fill="none" stroke="url(#evo-mark)" strokeWidth="4" strokeLinejoin="round" />
      <circle cx="32" cy="32" r="7" fill="url(#evo-mark)" />
      <path d="M32 25V14M38 36l9 5M26 36l-9 5" stroke="url(#evo-mark)" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

export function Logo({ href = '#/' }: { href?: string }) {
  return (
    <a href={href} className="group inline-flex items-center gap-2.5 rounded-lg" aria-label="Evo Code home">
      <LogoMark className="transition group-hover:scale-105" />
      <span className="font-mono text-sm font-bold tracking-[0.28em] text-zinc-100">EVO CODE</span>
    </a>
  )
}

export function Backdrop() {
  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden" aria-hidden>
      <div className="backdrop-grid absolute inset-0" />
      <div className="absolute -top-40 left-1/2 h-[520px] w-[900px] -translate-x-1/2 rounded-full bg-brand-600/20 blur-[120px]" />
      <div className="absolute -right-40 top-40 h-[380px] w-[520px] rounded-full bg-cyan-500/10 blur-[120px]" />
    </div>
  )
}
