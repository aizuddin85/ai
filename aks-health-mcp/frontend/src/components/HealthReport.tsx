/**
 * Renders the agent's markdown health report with severity-aware styling.
 *
 * While the query is in progress, shows a status message and spinner.
 * On error, shows a dismissible error card.
 */
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AlertCircle, CheckCircle, Clock, Loader2, XCircle } from 'lucide-react'
import type { QueryResult } from '@/types'

interface Props {
  result: QueryResult
}

function StatusBanner({ result }: Props) {
  if (result.status === 'loading' || result.status === 'streaming') {
    return (
      <div className="flex items-center gap-3 px-4 py-3 bg-azure-50 border border-azure-200
                      rounded-lg text-azure-700 text-sm animate-fade-in">
        <Loader2 className="w-4 h-4 animate-spin shrink-0" />
        <span>
          {result.statusMessage ?? 'Connecting to agent…'}
        </span>
      </div>
    )
  }

  if (result.status === 'error') {
    return (
      <div className="flex items-start gap-3 px-4 py-3 bg-red-50 border border-red-200
                      rounded-lg text-red-700 text-sm animate-fade-in">
        <XCircle className="w-4 h-4 shrink-0 mt-0.5" />
        <div>
          <p className="font-semibold">Agent error</p>
          <p className="text-red-600">{result.errorMessage}</p>
        </div>
      </div>
    )
  }

  if (result.status === 'done' && result.content) {
    const duration = result.completedAt && result.startedAt
      ? ((result.completedAt.getTime() - result.startedAt.getTime()) / 1000).toFixed(1)
      : null

    return (
      <div className="flex items-center gap-2 text-xs text-gray-400">
        <CheckCircle className="w-3.5 h-3.5 text-green-500" />
        <span>Report generated</span>
        {duration && (
          <>
            <span>·</span>
            <Clock className="w-3 h-3" />
            <span>{duration}s</span>
          </>
        )}
      </div>
    )
  }

  return null
}

export default function HealthReport({ result }: Props) {
  const isEmpty = !result.content && result.status !== 'loading' && result.status !== 'streaming'

  return (
    <div className="flex flex-col gap-4 animate-slide-up">
      {/* Query echo */}
      <div className="flex items-start gap-2">
        <div className="shrink-0 mt-0.5">
          <AlertCircle className="w-4 h-4 text-azure-500" />
        </div>
        <p className="text-sm font-medium text-gray-700 italic">"{result.query}"</p>
      </div>

      {/* Status banner */}
      <StatusBanner result={result} />

      {/* Markdown report */}
      {result.content && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
          <div className="px-6 py-5 health-prose">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {result.content}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {/* Still loading but no content yet */}
      {!result.content && (result.status === 'loading' || result.status === 'streaming') && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
          <div className="space-y-3">
            {[60, 45, 75, 35].map((w, i) => (
              <div key={i} className="flex gap-2">
                <div
                  className="h-3 bg-gray-100 rounded animate-pulse"
                  style={{ width: `${w}%` }}
                />
              </div>
            ))}
          </div>
          <p className="text-xs text-gray-400 mt-4 cursor-blink">
            Agent is querying Azure and cluster health
          </p>
        </div>
      )}
    </div>
  )
}
