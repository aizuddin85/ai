/**
 * Main sysadmin dashboard.
 *
 * Layout:
 *   ┌────────────────────────────────────────────────────┐
 *   │  Header (user info + sign-out)                     │
 *   ├──────────────┬─────────────────────────────────────┤
 *   │  Sidebar     │  Main area                          │
 *   │  - History   │  - Query input (sticky bottom)      │
 *   │              │  - Health report / welcome          │
 *   └──────────────┴─────────────────────────────────────┘
 */
import { useCallback, useState } from 'react'
import { Clock, ChevronRight, BarChart3, AlertTriangle, CheckCircle } from 'lucide-react'

import Header from '@/components/Header'
import QueryInput from '@/components/QueryInput'
import HealthReport from '@/components/HealthReport'
import { useAgentQuery } from '@/hooks/useAgentQuery'
import type { QueryResult, UserProfile } from '@/types'

interface Props { user: UserProfile }

function EmptyState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center px-8 py-16">
      <div className="w-16 h-16 bg-azure-50 rounded-2xl flex items-center justify-center mb-4">
        <BarChart3 className="w-8 h-8 text-azure-400" />
      </div>
      <h2 className="text-lg font-semibold text-gray-800 mb-2">
        AKS Health Dashboard
      </h2>
      <p className="text-gray-500 text-sm max-w-sm leading-relaxed">
        Ask any question about your AKS environment. The AI agent will query
        both Azure Resource Manager and the live Kubernetes API to give you
        a comprehensive health assessment.
      </p>
      <div className="mt-6 grid grid-cols-3 gap-3 w-full max-w-xs">
        {[
          { icon: CheckCircle, label: 'Cluster health', color: 'text-green-500' },
          { icon: AlertTriangle, label: 'Active alerts',  color: 'text-amber-500' },
          { icon: BarChart3,    label: 'Metrics',         color: 'text-azure-500' },
        ].map(({ icon: Icon, label, color }) => (
          <div key={label}
               className="flex flex-col items-center gap-1.5 p-3
                          bg-white rounded-xl border border-gray-200 text-xs text-gray-600">
            <Icon className={`w-5 h-5 ${color}`} />
            {label}
          </div>
        ))}
      </div>
    </div>
  )
}

function HistoryItem({
  result,
  active,
  onClick,
}: {
  result: QueryResult
  active: boolean
  onClick: () => void
}) {
  const statusColor =
    result.status === 'done'      ? 'bg-green-400' :
    result.status === 'error'     ? 'bg-red-400'   :
    result.status === 'loading' ||
    result.status === 'streaming' ? 'bg-azure-400 animate-pulse' :
                                    'bg-gray-300'

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 rounded-lg flex items-start gap-2
                  transition-colors text-sm group
                  ${active
                    ? 'bg-azure-50 text-azure-700 border border-azure-200'
                    : 'text-gray-600 hover:bg-gray-100 border border-transparent'
                  }`}
    >
      <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${statusColor}`} />
      <span className="flex-1 line-clamp-2 leading-snug">{result.query}</span>
      <ChevronRight className="w-3.5 h-3.5 shrink-0 mt-0.5 opacity-0 group-hover:opacity-50" />
    </button>
  )
}

export default function Dashboard({ user }: Props) {
  const { result, submit, cancel } = useAgentQuery()
  const [history, setHistory]     = useState<QueryResult[]>([])
  const [activeId, setActiveId]   = useState<string | null>(null)
  const [shownResult, setShownResult] = useState<QueryResult | null>(null)

  const isLoading =
    result?.status === 'loading' || result?.status === 'streaming'

  const handleSubmit = useCallback(
    async (query: string) => {
      await submit(query)
      // After submit() resolves the result state is updated via the hook.
      // We track history separately so old results stay visible.
    },
    [submit],
  )

  // Keep history in sync when a new result arrives
  const handleResultUpdate = useCallback((r: QueryResult | null) => {
    if (!r) return
    setHistory(prev => {
      const idx = prev.findIndex(h => h.id === r.id)
      if (idx === -1) return [r, ...prev]
      const next = [...prev]
      next[idx] = r
      return next
    })
    setActiveId(r.id)
    setShownResult(r)
  }, [])

  // Sync the hook result into local history
  if (result && result !== shownResult) {
    handleResultUpdate(result)
  }

  function viewHistoryItem(r: QueryResult) {
    setActiveId(r.id)
    setShownResult(r)
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-gray-50">
      <Header user={user} />

      <div className="flex flex-1 overflow-hidden">
        {/* ── Sidebar ──────────────────────────────────────────────── */}
        <aside className="w-60 shrink-0 bg-gray-900 flex flex-col border-r border-gray-700
                          overflow-hidden hidden md:flex">
          <div className="px-3 py-3 border-b border-gray-700">
            <div className="flex items-center gap-2 text-xs text-gray-400 uppercase tracking-wide font-medium">
              <Clock className="w-3.5 h-3.5" />
              Query history
            </div>
          </div>

          <nav className="flex-1 overflow-y-auto px-2 py-2 space-y-1">
            {history.length === 0 ? (
              <p className="text-xs text-gray-600 px-2 py-4 text-center leading-relaxed">
                Your queries will appear here.
              </p>
            ) : (
              history.map(r => (
                <HistoryItem
                  key={r.id}
                  result={r}
                  active={r.id === activeId}
                  onClick={() => viewHistoryItem(r)}
                />
              ))
            )}
          </nav>
        </aside>

        {/* ── Main content ─────────────────────────────────────────── */}
        <main className="flex-1 flex flex-col overflow-hidden">
          {/* Results area */}
          <div className="flex-1 overflow-y-auto">
            {shownResult ? (
              <div className="max-w-4xl mx-auto px-6 py-6">
                <HealthReport
                  key={shownResult.id}
                  result={
                    // Always show live result for the active query
                    result && result.id === shownResult.id ? result : shownResult
                  }
                />
              </div>
            ) : (
              <EmptyState />
            )}
          </div>

          {/* Query input – sticky at the bottom */}
          <div className="shrink-0 border-t border-gray-200 bg-white px-6 py-4 shadow-[0_-4px_6px_-1px_rgb(0,0,0,0.05)]">
            <div className="max-w-4xl mx-auto">
              <QueryInput
                onSubmit={handleSubmit}
                onCancel={cancel}
                isLoading={isLoading}
              />
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
