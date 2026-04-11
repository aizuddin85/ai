// ----------------------------------------------------------------
// Shared TypeScript types
// ----------------------------------------------------------------

export interface UserProfile {
  oid: string
  upn: string
  name: string
  email: string
  authorized: boolean
}

// SSE event types sent by the backend
export type SseEventType = 'started' | 'status' | 'result' | 'error' | 'done'

export interface SseEvent {
  type: SseEventType
  query_id: string
  message?: string   // status / error events
  content?: string   // result events (markdown)
}

export type QueryStatus = 'idle' | 'loading' | 'streaming' | 'done' | 'error'

export interface QueryResult {
  id: string
  query: string
  status: QueryStatus
  statusMessage?: string
  content?: string
  errorMessage?: string
  startedAt: Date
  completedAt?: Date
}

export interface SuggestedQuery {
  label: string
  query: string
  icon: string
}

export const SUGGESTED_QUERIES: SuggestedQuery[] = [
  {
    label: 'Overall health',
    query: 'What is the overall health of my AKS environment? List any critical issues and recommended actions.',
    icon: '🩺',
  },
  {
    label: 'Unhealthy pods',
    query: 'Show me all pods that are not in Running or Succeeded state. Include the reason and restart counts.',
    icon: '🚨',
  },
  {
    label: 'Node status',
    query: 'List all nodes and their status. Highlight any nodes that are Not Ready or under memory/CPU pressure.',
    icon: '🖥️',
  },
  {
    label: 'Resource health events',
    query: 'Are there any active Azure Resource Health incidents or scheduled maintenance events affecting my AKS clusters?',
    icon: '⚠️',
  },
  {
    label: 'Upgrade check',
    query: 'Which AKS clusters have Kubernetes version upgrades available? List the current and available versions.',
    icon: '🔄',
  },
  {
    label: 'Warning events',
    query: 'Show the last 50 Warning events in the cluster. Group them by reason and identify the most frequent issues.',
    icon: '📋',
  },
]
