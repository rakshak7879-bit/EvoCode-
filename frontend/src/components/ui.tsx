import { LoaderCircle, TriangleAlert } from 'lucide-react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { cn } from '../lib/format'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  primary:
    'bg-linear-to-r from-brand-500 to-cyan-500 text-white shadow-[0_8px_30px_-8px_rgba(139,92,246,0.6)] hover:brightness-110',
  secondary: 'border border-white/10 bg-white/[0.04] text-zinc-100 hover:bg-white/[0.08]',
  ghost: 'text-zinc-300 hover:bg-white/[0.06] hover:text-white',
  danger: 'border border-amber-400/30 bg-amber-400/10 text-amber-200 hover:bg-amber-400/15',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
  icon?: ReactNode
}

export function Button({ variant = 'secondary', size = 'md', loading, icon, className, children, disabled, ...rest }: ButtonProps) {
  const sizes = { sm: 'h-8 px-3 text-xs gap-1.5', md: 'h-10 px-4 text-sm gap-2', lg: 'h-12 px-6 text-base gap-2.5' }
  return (
    <button
      type="button"
      className={cn(
        'inline-flex items-center justify-center rounded-xl font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
        sizes[size],
        BUTTON_STYLES[variant],
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  )
}

export function Panel({ className, children, as: Tag = 'section' }: { className?: string; children: ReactNode; as?: 'section' | 'div' | 'article' }) {
  return <Tag className={cn('panel', className)}>{children}</Tag>
}

export function PanelHeader({ icon, title, subtitle, actions }: { icon?: ReactNode; title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-white/5 px-5 py-4">
      <div className="flex min-w-0 items-start gap-3">
        {icon && <span className="mt-0.5 text-brand-300" aria-hidden>{icon}</span>}
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-zinc-100">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-zinc-500">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  )
}

export function Chip({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[11px] font-medium text-zinc-300',
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-zinc-400" role="status">
      <LoaderCircle className="size-4 animate-spin text-brand-300" aria-hidden />
      {label ?? 'Loading…'}
    </span>
  )
}

export function ErrorBanner({ title, message, action }: { title: string; message?: string | null; action?: ReactNode }) {
  return (
    <div role="alert" className="flex flex-wrap items-start gap-3 rounded-xl border border-amber-400/25 bg-amber-400/[0.07] px-4 py-3">
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-300" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-amber-100">{title}</p>
        {message && <p className="mt-0.5 text-sm text-amber-200/80">{message}</p>}
      </div>
      {action}
    </div>
  )
}

export function EmptyState({ icon, title, message, action }: { icon: ReactNode; title: string; message?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <span className="grid size-12 place-items-center rounded-2xl border border-white/10 bg-white/[0.03] text-zinc-400" aria-hidden>
        {icon}
      </span>
      <p className="text-sm font-medium text-zinc-200">{title}</p>
      {message && <p className="max-w-md text-sm text-zinc-500">{message}</p>}
      {action}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-lg bg-white/[0.05]', className)} aria-hidden />
}
