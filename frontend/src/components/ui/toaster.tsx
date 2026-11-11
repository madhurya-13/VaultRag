import { Toaster as SonnerToaster } from 'sonner'

import { useTheme } from '@/contexts/theme-context'

export function Toaster() {
  const { theme } = useTheme()
  return (
    <SonnerToaster
      theme={theme}
      position="top-right"
      richColors
      closeButton
      toastOptions={{
        classNames: {
          toast: 'font-sans',
        },
      }}
    />
  )
}
