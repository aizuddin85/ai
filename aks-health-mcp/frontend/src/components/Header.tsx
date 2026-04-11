/**
 * Top navigation bar.
 * Shows the app name, current user, and sign-out button.
 */
import { LogOut, Activity } from 'lucide-react'
import { useMsal } from '@azure/msal-react'
import type { UserProfile } from '@/types'

interface Props { user: UserProfile }

export default function Header({ user }: Props) {
  const { instance } = useMsal()
  const appName = (import.meta.env.VITE_APP_NAME as string | undefined) ?? 'AKS Health Dashboard'

  return (
    <header className="h-14 bg-gray-900 border-b border-gray-700 flex items-center px-4 gap-3 shrink-0">
      {/* Logo + name */}
      <div className="flex items-center gap-2 text-white font-semibold text-sm">
        <Activity className="w-5 h-5 text-azure-400" />
        <span>{appName}</span>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* User info */}
      <div className="flex items-center gap-3">
        {/* Avatar */}
        <div className="w-7 h-7 rounded-full bg-azure-500 flex items-center justify-center
                        text-white text-xs font-bold uppercase">
          {user.name.charAt(0)}
        </div>
        <div className="hidden sm:block text-right">
          <p className="text-white text-xs font-medium leading-tight">{user.name}</p>
          <p className="text-gray-400 text-xs leading-tight">{user.email}</p>
        </div>

        {/* Sign-out */}
        <button
          onClick={() => instance.logoutRedirect()}
          title="Sign out"
          className="ml-2 p-1.5 rounded-md text-gray-400 hover:text-white hover:bg-gray-700
                     transition-colors"
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </header>
  )
}
