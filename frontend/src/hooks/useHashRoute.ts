import { useEffect, useState } from 'react'

export const TABS = ['overview', 'findings', 'memory', 'walkthrough'] as const
export type Tab = (typeof TABS)[number]

export type Route = { page: 'home' } | { page: 'repo'; id: string; tab: Tab }

function parse(hash: string): Route {
  const match = hash.match(/^#\/repo\/([a-f0-9]{8,32})(?:\/([a-z]+))?/)
  if (match) {
    const tab = (TABS as readonly string[]).includes(match[2] ?? '') ? (match[2] as Tab) : 'overview'
    return { page: 'repo', id: match[1], tab }
  }
  return { page: 'home' }
}

export function routeHref(route: Route): string {
  return route.page === 'home' ? '#/' : `#/repo/${route.id}/${route.tab}`
}

export function navigate(route: Route): void {
  window.location.hash = routeHref(route)
}

/** Minimal hash router: survives refreshes and needs no extra dependency. */
export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
