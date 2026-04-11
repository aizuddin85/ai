/**
 * useAgentQuery
 *
 * Manages the lifecycle of a single RootAgent query:
 *   idle → loading → streaming → done | error
 *
 * Returns a submit function and the current QueryResult state.
 */
import { useCallback, useRef, useState } from 'react'

import { useMsal } from '@azure/msal-react'
import type { AccountInfo } from '@azure/msal-browser'

import { apiTokenRequest } from '@/authConfig'
import { ApiError, queryAgent } from '@/api/agentApi'
import type { QueryResult, SseEvent } from '@/types'

interface UseAgentQuery {
  result: QueryResult | null
  submit: (query: string) => Promise<void>
  cancel:  () => void
}

export function useAgentQuery(): UseAgentQuery {
  const { instance, accounts } = useMsal()
  const [result, setResult] = useState<QueryResult | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const cancel = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const submit = useCallback(async (query: string) => {
    // Cancel any in-flight request
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const queryId = crypto.randomUUID()
    setResult({
      id: queryId,
      query,
      status: 'loading',
      startedAt: new Date(),
    })

    const account: AccountInfo | undefined = accounts[0]
    if (!account) {
      setResult(prev => prev
        ? { ...prev, status: 'error', errorMessage: 'Not authenticated' }
        : null)
      return
    }

    let token: string
    try {
      const resp = await instance.acquireTokenSilent({
        ...apiTokenRequest,
        account,
      })
      token = resp.accessToken
    } catch {
      setResult(prev => prev
        ? { ...prev, status: 'error', errorMessage: 'Failed to acquire token' }
        : null)
      return
    }

    const handleEvent = (event: SseEvent) => {
      setResult(prev => {
        if (!prev) return prev
        switch (event.type) {
          case 'started':
            return { ...prev, status: 'streaming' }
          case 'status':
            return { ...prev, statusMessage: event.message }
          case 'result':
            return { ...prev, content: event.content, statusMessage: undefined }
          case 'error':
            return {
              ...prev,
              status: 'error',
              errorMessage: event.message,
              completedAt: new Date(),
            }
          case 'done':
            return {
              ...prev,
              status: prev.content ? 'done' : prev.status,
              completedAt: new Date(),
            }
          default:
            return prev
        }
      })
    }

    try {
      await queryAgent(query, token, handleEvent, controller.signal)
      // Ensure final status is 'done' if content arrived
      setResult(prev =>
        prev && prev.status === 'streaming'
          ? { ...prev, status: 'done', completedAt: new Date() }
          : prev
      )
    } catch (err) {
      if (controller.signal.aborted) return  // user cancelled
      const msg = err instanceof ApiError ? err.message : String(err)
      setResult(prev => prev
        ? { ...prev, status: 'error', errorMessage: msg, completedAt: new Date() }
        : null)
    }
  }, [instance, accounts])

  return { result, submit, cancel }
}
