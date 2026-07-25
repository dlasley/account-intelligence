'use client'

import { useRouter } from 'next/navigation'
import { Button } from '@/components/ui/button'
import type { VariantProps } from 'class-variance-authority'
import type { buttonVariants } from '@/components/ui/button'

/** Re-runs the current route's Server Component to retry a failed data fetch. */
export default function RetryButton({
  children = 'Retry',
  ...props
}: React.ComponentProps<'button'> & VariantProps<typeof buttonVariants>) {
  const router = useRouter()
  return (
    <Button onClick={() => router.refresh()} {...props}>
      {children}
    </Button>
  )
}
