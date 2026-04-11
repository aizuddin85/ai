/**
 * Login page – shown to unauthenticated visitors.
 * Triggers the MSAL redirect flow on button click.
 */
import { useState } from 'react'
import { useMsal } from '@azure/msal-react'
import { loginRequest } from '@/authConfig'

export default function LoginPage() {
  const { instance } = useMsal()
  const [loading, setLoading] = useState(false)

  const appName = (import.meta.env.VITE_APP_NAME as string | undefined) ?? 'AKS Health Dashboard'

  async function handleLogin() {
    setLoading(true)
    try {
      await instance.loginRedirect(loginRequest)
    } catch (err) {
      console.error('Login failed', err)
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-azure-900 via-azure-800 to-azure-700 flex items-center justify-center p-4">
      {/* Background decoration */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-azure-600 opacity-20 rounded-full blur-3xl" />
        <div className="absolute -bottom-40 -left-40 w-96 h-96 bg-azure-500 opacity-20 rounded-full blur-3xl" />
      </div>

      <div className="relative w-full max-w-md animate-fade-in">
        {/* Card */}
        <div className="bg-white rounded-2xl shadow-2xl overflow-hidden">
          {/* Top bar */}
          <div className="bg-azure-500 px-8 py-6 text-white">
            <div className="flex items-center gap-3 mb-2">
              {/* Azure Kubernetes Service icon (simplified SVG) */}
              <svg viewBox="0 0 32 32" className="w-10 h-10 fill-current" aria-hidden="true">
                <rect width="32" height="32" rx="6" className="fill-white opacity-20" />
                <path d="M16 5 L27 11 L27 21 L16 27 L5 21 L5 11 Z"
                      fill="none" stroke="white" strokeWidth="1.5" />
                <circle cx="16" cy="16" r="4" fill="white" />
                <circle cx="16" cy="8"  r="2" fill="white" opacity="0.7" />
                <circle cx="23" cy="12" r="2" fill="white" opacity="0.7" />
                <circle cx="23" cy="20" r="2" fill="white" opacity="0.7" />
                <circle cx="16" cy="24" r="2" fill="white" opacity="0.7" />
                <circle cx="9"  cy="20" r="2" fill="white" opacity="0.7" />
                <circle cx="9"  cy="12" r="2" fill="white" opacity="0.7" />
              </svg>
              <div>
                <h1 className="text-2xl font-bold tracking-tight">{appName}</h1>
                <p className="text-azure-100 text-sm">SysAdmin Portal</p>
              </div>
            </div>
          </div>

          {/* Body */}
          <div className="px-8 py-8">
            <p className="text-gray-600 mb-6 leading-relaxed">
              Sign in with your Microsoft account to access the AKS health monitoring dashboard.
              Access is restricted to authorised personnel only.
            </p>

            <button
              onClick={handleLogin}
              disabled={loading}
              className="w-full flex items-center justify-center gap-3 bg-azure-500 hover:bg-azure-600
                         disabled:opacity-60 disabled:cursor-not-allowed
                         text-white font-semibold py-3 px-6 rounded-lg
                         transition-colors duration-150 shadow-md hover:shadow-lg"
            >
              {loading ? (
                <>
                  <span className="animate-spin w-5 h-5 border-2 border-white border-t-transparent rounded-full" />
                  Redirecting to Microsoft…
                </>
              ) : (
                <>
                  {/* Microsoft logo */}
                  <svg viewBox="0 0 21 21" className="w-5 h-5" aria-hidden="true">
                    <rect x="1"  y="1"  width="9" height="9" fill="#f25022" />
                    <rect x="11" y="1"  width="9" height="9" fill="#7fba00" />
                    <rect x="1"  y="11" width="9" height="9" fill="#00a4ef" />
                    <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
                  </svg>
                  Sign in with Microsoft
                </>
              )}
            </button>

            <p className="text-center text-xs text-gray-400 mt-6">
              By signing in you agree to the organisation's acceptable use policy.
              All access is logged and monitored.
            </p>
          </div>
        </div>

        {/* Footer */}
        <p className="text-center text-azure-200 text-xs mt-6">
          Powered by Azure AI Foundry &nbsp;·&nbsp; AKS Health MCP Server
        </p>
      </div>
    </div>
  )
}
