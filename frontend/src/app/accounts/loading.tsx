import { Skeleton } from '@/components/ui/skeleton'

export default function AccountsLoading() {
  return (
    <main className="p-8">
      <div className="mb-6 flex items-baseline gap-2">
        <Skeleton className="h-7 w-28" />
        <Skeleton className="h-4 w-16" />
      </div>
      <div className="mb-4 flex flex-wrap gap-2">
        <Skeleton className="h-8 min-w-[200px] flex-1" />
        <Skeleton className="h-8 w-28" />
        <Skeleton className="h-8 w-28" />
        <Skeleton className="h-8 w-40" />
      </div>
      <div className="overflow-hidden rounded-lg border border-border">
        <div className="flex items-center gap-6 border-b border-border bg-muted/30 px-4 py-2.5">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-16" />
        </div>
        <div className="divide-y divide-border">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-3">
              <Skeleton className="h-4 w-1/5" />
              <Skeleton className="h-4 w-1/6" />
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="h-4 w-14" />
              <Skeleton className="h-4 flex-1" />
            </div>
          ))}
        </div>
      </div>
    </main>
  )
}
