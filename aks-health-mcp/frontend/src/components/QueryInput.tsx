/**
 * Query input form with suggested queries sidebar.
 */
import { FormEvent, useState } from 'react'
import { Send, X, Zap } from 'lucide-react'
import { SUGGESTED_QUERIES } from '@/types'

interface Props {
  onSubmit: (query: string) => void
  onCancel?: () => void
  isLoading: boolean
  disabled?: boolean
}

export default function QueryInput({ onSubmit, onCancel, isLoading, disabled }: Props) {
  const [query, setQuery] = useState('')

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = query.trim()
    if (trimmed.length < 3) return
    onSubmit(trimmed)
  }

  function pickSuggestion(text: string) {
    setQuery(text)
  }

  return (
    <div className="space-y-3">
      {/* Text input form */}
      <form onSubmit={handleSubmit} className="flex gap-2">
        <div className="flex-1 relative">
          <textarea
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                handleSubmit(e as unknown as FormEvent)
              }
            }}
            disabled={disabled || isLoading}
            placeholder="Ask a question about your AKS environment…"
            rows={2}
            className="w-full resize-none rounded-lg border border-gray-300 bg-white px-4 py-3
                       text-sm text-gray-900 placeholder-gray-400
                       focus:outline-none focus:ring-2 focus:ring-azure-500 focus:border-transparent
                       disabled:bg-gray-50 disabled:text-gray-400
                       transition-shadow"
          />
          <p className="absolute bottom-2 right-3 text-xs text-gray-300 pointer-events-none">
            ↵ Send
          </p>
        </div>

        {isLoading ? (
          <button
            type="button"
            onClick={onCancel}
            className="self-end px-4 py-3 rounded-lg bg-red-100 hover:bg-red-200
                       text-red-600 transition-colors"
            title="Cancel query"
          >
            <X className="w-5 h-5" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!query.trim() || disabled}
            className="self-end px-4 py-3 rounded-lg bg-azure-500 hover:bg-azure-600
                       disabled:opacity-40 disabled:cursor-not-allowed
                       text-white transition-colors shadow-sm"
            title="Submit query"
          >
            <Send className="w-5 h-5" />
          </button>
        )}
      </form>

      {/* Suggested queries */}
      <div>
        <div className="flex items-center gap-1.5 mb-2">
          <Zap className="w-3.5 h-3.5 text-gray-400" />
          <span className="text-xs text-gray-400 font-medium uppercase tracking-wide">
            Suggested queries
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {SUGGESTED_QUERIES.map(sq => (
            <button
              key={sq.label}
              onClick={() => pickSuggestion(sq.query)}
              disabled={isLoading || disabled}
              className="inline-flex items-center gap-1.5 px-3 py-1.5
                         bg-gray-50 hover:bg-azure-50 hover:border-azure-300
                         border border-gray-200 rounded-full text-xs text-gray-600
                         hover:text-azure-700 transition-colors disabled:opacity-40
                         disabled:cursor-not-allowed"
            >
              <span>{sq.icon}</span>
              {sq.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
