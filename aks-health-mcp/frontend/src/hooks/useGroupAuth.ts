/**
 * useGroupAuth
 *
 * After MSAL completes authentication, acquires an API-scoped token and
 * calls GET /api/auth/me to verify:
 *   1. The token is valid.
 *   2. The user is a member of the required AD group.
 *
 * Returns the user profile or an error / loading state.
 */
import { useEffect, useState } from 'react'

import { useMsal } from '@azure/msal-react'
import type { AccountInfo } from '@azure/msal-browser'

import { apiTokenRequest } from '@/authConfig'
import { ApiError, getMe } from '@/api/agentApi'
import type { UserProfile } from '@/types'

export type GroupAuthState =
  | { status: 'loading' }
  | { status: 'authorized'; user: UserProfile }
  | { status: 'unauthorized'; reason: string }
  | { status: 'error'; error: Error }

export function useGroupAuth(): GroupAuthState {
  const { instance, accounts } = useMsal()
  const [state, setState] = useState<GroupAuthState>({ status: 'loading' })

  useEffect(() => {
    if (accounts.length === 0) {
      setState({ status: 'loading' })
      return
    }

    let cancelled = false
    const account: AccountInfo = accounts[0]

    async function checkAuth() {
      try {
        // Acquire token silently (uses refresh token / session if available)
        const tokenResponse = await instance.acquireTokenSilent({
          ...apiTokenRequest,
          account,
        })
        const token = tokenResponse.accessToken

        if (cancelled) return

        const user = await getMe(token)
        if (!cancelled) setState({ status: 'authorized', user })

      } catch (err) {
        if (cancelled) return

        if (err instanceof ApiError) {
          if (err.isForbidden) {
            setState({
              status: 'unauthorized',
              reason: err.message,
            })
            return
          }
          if (err.isUnauthorized) {
            // Token expired or revoked – trigger interactive re-auth
            try {
              await instance.acquireTokenRedirect({ ...apiTokenRequest, account })
            } catch { /* redirect will handle this */ }
            return
          }
        }

        // Consent required or no cached token → interactive login
        if (err instanceof Error && err.name === 'InteractionRequiredAuthError') {
          try {
            await instance.acquireTokenRedirect({ ...apiTokenRequest, account })
          } catch { /* redirect in progress */ }
          return
        }

        setState({ status: 'error', error: err instanceof Error ? err : new Error(String(err)) })
      }
    }

    checkAuth()
    return () => { cancelled = true }
  }, [instance, accounts])

  return state
}
