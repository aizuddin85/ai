/**
 * Backend API client.
 *
 * All requests attach the Azure AD bearer token acquired by MSAL.
 * The getToken() callback is injected so this module has no direct
 * MSAL dependency (easier to test).
 */
import type { SseEvent, UserProfile } from '@/types'

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''

// ── Helpers ──────────────────────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization:  `Bearer ${token}`,
      ...init?.headers,
    },
  })

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json() as { detail?: string }
      if (body.detail) detail = body.detail
    } catch { /* ignore */ }
    throw new ApiError(resp.status, detail)
  }

  return resp.json() as Promise<T>
}

// ── Error class ───────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  get isForbidden(): boolean { return this.status === 403 }
  get isUnauthorized(): boolean { return this.status === 401 }
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function getMe(token: string): Promise<UserProfile> {
  return apiFetch<UserProfile>('/api/auth/me', token)
}

// ── Agent ─────────────────────────────────────────────────────────────────────

/**
 * Submit a query to the RootAgent via SSE.
 *
 * Calls onEvent for each parsed SSE event.
 * Resolves when the stream ends or rejects on network error.
 *
 * @param query     Natural-language AKS health question
 * @param token     Bearer token for the backend API
 * @param onEvent   Callback for each parsed SSE event
 * @param signal    Optional AbortSignal to cancel the request
 */
export async function queryAgent(
  query: string,
  token: string,
  onEvent: (event: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${BASE}/api/agent/query`, {
    method:  'POST',
    headers: {
      'Content-Type':  'application/json',
      Authorization:   `Bearer ${token}`,
      Accept:          'text/event-stream',
    },
    body:   JSON.stringify({ query }),
    signal,
  })

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json() as { detail?: string }
      if (body.detail) detail = body.detail
    } catch { /* ignore */ }
    throw new ApiError(resp.status, detail)
  }

  if (!resp.body) throw new Error('Response body is null')

  const reader  = resp.body.getReader()
  const decoder = new TextDecoder()
  let   buffer  = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // SSE events are separated by '\n\n'
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''  // keep incomplete last part

      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data:')) continue
        const json = line.slice(5).trim()
        if (json === '[DONE]') return
        try {
          onEvent(JSON.parse(json) as SseEvent)
        } catch { /* malformed event – skip */ }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
