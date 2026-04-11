interface Props {
  message?: string
  size?: 'sm' | 'md' | 'lg'
}

export default function LoadingSpinner({ message, size = 'md' }: Props) {
  const sz = { sm: 'w-5 h-5', md: 'w-8 h-8', lg: 'w-12 h-12' }[size]

  return (
    <div className="flex flex-col items-center justify-center gap-3">
      <div
        className={`${sz} border-4 border-azure-200 border-t-azure-500
                    rounded-full animate-spin`}
        role="status"
        aria-label="Loading"
      />
      {message && (
        <p className="text-sm text-gray-500 animate-pulse">{message}</p>
      )}
    </div>
  )
}
