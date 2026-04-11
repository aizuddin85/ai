/**
 * Shown when the user authenticated successfully but is not a member
 * of the required AD security group.
 */
import { useMsal } from '@azure/msal-react'
import { ShieldX, LogOut } from 'lucide-react'

interface Props { reason?: string }

export default function AccessDenied({ reason }: Props) {
  const { instance } = useMsal()

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl max-w-md w-full p-8 text-center animate-fade-in">
        <div className="flex justify-center mb-4">
          <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center">
            <ShieldX className="w-8 h-8 text-red-500" />
          </div>
        </div>

        <h1 className="text-2xl font-bold text-gray-900 mb-2">Access Denied</h1>
        <p className="text-gray-500 mb-4 leading-relaxed">
          Your account is authenticated but you are not a member of the
          authorised group for this application.
        </p>

        {reason && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700 mb-6 text-left">
            <span className="font-semibold">Reason: </span>{reason}
          </div>
        )}

        <p className="text-sm text-gray-400 mb-6">
          Contact your system administrator to request access.
        </p>

        <button
          onClick={() => instance.logoutRedirect()}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg
                     border border-gray-300 text-gray-700 hover:bg-gray-50
                     transition-colors text-sm font-medium"
        >
          <LogOut className="w-4 h-4" />
          Sign out
        </button>
      </div>
    </div>
  )
}
