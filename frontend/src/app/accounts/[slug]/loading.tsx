import { Skeleton } from '@/components/ui/skeleton'

export default function Loading() {
  return (
    <main className="max-w-6xl p-8" aria-busy="true">
      <span className="sr-only" role="status">
        Loading account…
      </span>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-5 w-28" />
      </div>
      <Skeleton className="mb-6 h-9 w-full max-w-md" />
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="space-y-6">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
        <Skeleton className="h-80 w-full" />
      </div>
    </main>
  )
}
