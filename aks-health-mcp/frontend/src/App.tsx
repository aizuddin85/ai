/**
 * Application root – sets up MSAL provider and React Router.
 *
 * Routes:
 *   /                    → ProtectedRoute → Dashboard
 *   /auth/callback       → Handled by MSAL (redirectUri)
 *   *                    → Redirect to /
 */
import { MsalProvider }       from '@azure/msal-react'
import { PublicClientApplication } from '@azure/msal-browser'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { msalConfig }   from '@/authConfig'
import ProtectedRoute   from '@/components/ProtectedRoute'
import Dashboard        from '@/components/Dashboard'

// Instantiate MSAL outside of the component tree so it persists across renders
const msalInstance = new PublicClientApplication(msalConfig)

export default function App() {
  return (
    <MsalProvider instance={msalInstance}>
      <BrowserRouter>
        <Routes>
          {/* Main app – requires auth + group membership */}
          <Route
            path="/"
            element={
              <ProtectedRoute>
                {user => <Dashboard user={user} />}
              </ProtectedRoute>
            }
          />

          {/* MSAL redirect callback – MSAL handles the hash/code automatically */}
          <Route path="/auth/callback" element={<Navigate to="/" replace />} />

          {/* Catch-all */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </MsalProvider>
  )
}
