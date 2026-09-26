import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, errorMessage } from '../api'
import type { FindingsResponse, RepositoryStatus, WalkthroughResponse } from '../types'

const POLL_MS = 600

export interface RepositoryState {
  status: RepositoryStatus | null
  error: string | null
  notFound: boolean
  findings: FindingsResponse | null
  findingsError: string | null
  walkthrough: WalkthroughResponse | null
  walkthroughError: string | null
  refreshFindings: () => Promise<FindingsResponse | null>
  reanalyze: () => Promise<void>
}

/** Polls analysis status until it settles, then loads findings and the walkthrough. */
export function useRepository(id: string): RepositoryState {
  const [status, setStatus] = useState<RepositoryStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [findings, setFindings] = useState<FindingsResponse | null>(null)
  const [findingsError, setFindingsError] = useState<string | null>(null)
  const [walkthrough, setWalkthrough] = useState<WalkthroughResponse | null>(null)
  const [walkthroughError, setWalkthroughError] = useState<string | null>(null)
  const [pollKey, setPollKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    const tick = async () => {
      try {
        const next = await api.status(id)
        if (cancelled) return
        setStatus(next)
        setError(null)
        setNotFound(false)
        if (next.status === 'queued' || next.status === 'processing') timer = window.setTimeout(tick, POLL_MS)
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && (err.status === 404 || err.status === 422)) {
          setNotFound(true)
          return
        }
        setError(errorMessage(err))
        timer = window.setTimeout(tick, 2500)
      }
    }
    void tick()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [id, pollKey])

  const completedRun = status?.status === 'completed' ? status.analysis_count : null

  useEffect(() => {
    if (completedRun === null) return
    let cancelled = false
    api
      .findings(id)
      .then((data) => {
        if (!cancelled) {
          setFindings(data)
          setFindingsError(null)
        }
      })
      .catch((err) => !cancelled && setFindingsError(errorMessage(err)))
    api
      .walkthrough(id)
      .then((data) => {
        if (!cancelled) {
          setWalkthrough(data)
          setWalkthroughError(null)
        }
      })
      .catch((err) => !cancelled && setWalkthroughError(errorMessage(err)))
    return () => {
      cancelled = true
    }
  }, [id, completedRun])

  const refreshFindings = useCallback(async () => {
    try {
      const [data, nextStatus] = await Promise.all([api.findings(id), api.status(id)])
      setFindings(data)
      setStatus(nextStatus)
      setFindingsError(null)
      return data
    } catch (err) {
      setFindingsError(errorMessage(err))
      return null
    }
  }, [id])

  const reanalyze = useCallback(async () => {
    await api.reanalyze(id)
    setFindings(null)
    setWalkthrough(null)
    setWalkthroughError(null)
    setPollKey((key) => key + 1)
  }, [id])

  return { status, error, notFound, findings, findingsError, walkthrough, walkthroughError, refreshFindings, reanalyze }
}
