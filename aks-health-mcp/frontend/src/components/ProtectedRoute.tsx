/**
 * ProtectedRoute
 *
 * Gate that wraps any route requiring authentication + group membership.
 *
 * State machine:
 *   MSAL not authenticated → LoginPage (redirect to Azure AD)
 *   MSAL authenticated, group check loading → LoadingSpinner
 *   MSAL authenticated, group check authorized → children
 *   MSAL authenticated, group check unauthorized → AccessDenied
 *   Error → error message
 */
import { useIsAuthenticated, useMsalAuthentication } from '@azure/msal-react'
import { InteractionType }   from '@azure/msal-browser'
import { loginRequest }      from '@/authConfig'
import { useGroupAuth }      from '@/hooks/useGroupAuth'
import LoadingSpinner        from '@/components/LoadingSpinner'
import AccessDenied          from '@/components/AccessDenied'
import type { UserProfile }  from '@/types'

interface Props {
  children: (user: UserProfile) => React.ReactNode
}

export default function ProtectedRoute({ children }: Props) {
  // Trigger MSAL redirect login if not authenticated
  useMsalAuthentication(InteractionType.Redirect, loginRequest)
  const isAuthenticated = useIsAuthenticated()
  const authState       = useGroupAuth()

  // Not yet authenticated – MSAL redirect is in progress
  if (!isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <LoadingSpinner message="Redirecting to Microsoft login…" size="lg" />
      </div>
    )
  }

  // Authenticated but awaiting group check result
  if (authState.status === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <LoadingSpinner message="Verifying authorisation…" size="lg" />
      </div>
    )
  }

  if (authState.status === 'unauthorized') {
    return <AccessDenied reason={authState.reason} />
  }

  if (authState.status === 'error') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="bg-white rounded-xl shadow p-8 max-w-md text-center">
          <p className="text-red-600 font-semibold mb-2">Authentication error</p>
          <p className="text-gray-500 text-sm">{authState.error.message}</p>
        </div>
      </div>
    )
  }

  // Authorized ✓
  return <>{children(authState.user)}</>
}
