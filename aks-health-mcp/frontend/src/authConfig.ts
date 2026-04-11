/**
 * MSAL configuration for Azure AD SSO.
 *
 * The app registration must be configured as a Single-Page Application (SPA)
 * with the following redirect URIs:
 *   - http://localhost:5173/auth/callback   (dev)
 *   - https://<your-domain>/auth/callback   (prod)
 *
 * Required API permissions (delegated):
 *   - openid, profile, email  (Microsoft Graph – sign-in)
 *   - api://<backend-client-id>/user_impersonation  (backend API)
 *   - GroupMember.Read.All (if group claim fallback via Graph is needed)
 *
 * Token configuration:
 *   - App registration → Token configuration → Add groups claim → Security groups
 */

import {
  BrowserCacheLocation,
  Configuration,
  LogLevel,
  PopupRequest,
  RedirectRequest,
} from '@azure/msal-browser'

const tenantId  = import.meta.env.VITE_AZURE_AD_TENANT_ID  as string
const clientId  = import.meta.env.VITE_AZURE_AD_CLIENT_ID   as string

if (!tenantId || !clientId) {
  throw new Error(
    'Missing VITE_AZURE_AD_TENANT_ID or VITE_AZURE_AD_CLIENT_ID env variables.'
  )
}

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority:   `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: `${window.location.origin}/auth/callback`,
    postLogoutRedirectUri: window.location.origin,
    navigateToLoginRequestUrl: true,
  },
  cache: {
    cacheLocation:      BrowserCacheLocation.SessionStorage,
    storeAuthStateInCookie: false,  // true for IE11 compat if needed
  },
  system: {
    loggerOptions: {
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) return
        if (import.meta.env.DEV) {
          const levelStr = LogLevel[level]
          console.debug(`[MSAL:${levelStr}] ${message}`)
        }
      },
      logLevel: import.meta.env.DEV ? LogLevel.Warning : LogLevel.Error,
    },
  },
}

/**
 * Scopes for the backend API token.
 * The backend validates tokens with aud == clientId or api://<clientId>.
 */
export const apiTokenRequest: PopupRequest & RedirectRequest = {
  scopes: [`api://${clientId}/user_impersonation`],
  // Fallback to login scopes if the API scope isn't consented yet
  extraScopesToConsent: ['openid', 'profile', 'email'],
}

/**
 * Scopes for the initial login (id_token with user profile + groups).
 */
export const loginRequest: PopupRequest & RedirectRequest = {
  scopes: ['openid', 'profile', 'email'],
  prompt: 'select_account',
}
