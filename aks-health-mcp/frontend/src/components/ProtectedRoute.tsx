/**
 * ProtectedRoute
 *
 * Gate that wraps any route requiring Azure AD authentication.
 *
 * State machine:
 *   MSAL not authenticated → redirect to Azure AD login
 *   MSAL authenticated, backend check loading → LoadingSpinner
 *   MSAL authenticated, backend confirmed → children
 *   Error → error message
 *
 * Any authenticated Azure AD user is allowed through.  Resource-level
 * access is controlled by Azure RBAC at query time — no AD group check.
 */
import { useIsAuthenticated, useMsalAuthentication } from '@azure/msal-react'
import { InteractionType }   from '@azure/msal-browser'
import { loginRequest }      from '@/authConfig'
import { useGroupAuth }      from '@/hooks/useGroupAuth'
import LoadingSpinner        from '@/components/LoadingSpinner'
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

  // Authenticated but awaiting backend confirmation
  if (authState.status === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <LoadingSpinner message="Verifying authentication…" size="lg" />
      </div>
    )
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
