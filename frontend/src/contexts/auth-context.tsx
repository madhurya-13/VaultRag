import { useQueryClient } from '@tanstack/react-query'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import {
  api,
  clearStoredToken,
  getStoredToken,
  storeToken,
} from '@/lib/api'
import type { LoginRequest, Me, TokenResponse } from '@/types/api'

type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

interface AuthContextValue {
  user: Me | null
  status: AuthStatus
  isAuthenticated: boolean
  login: (payload: LoginRequest) => Promise<Me>
  logout: () => void
  refreshMe: () => Promise<Me | null>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [user, setUser] = useState<Me | null>(null)
  const [status, setStatus] = useState<AuthStatus>('loading')

  const clearSession = useCallback(() => {
    clearStoredToken()
    setUser(null)
    setStatus('unauthenticated')
    queryClient.clear()
  }, [queryClient])

  const fetchMe = useCallback(async (): Promise<Me | null> => {
    if (!getStoredToken()) {
      setUser(null)
      setStatus('unauthenticated')
      return null
    }
    try {
      const { data } = await api.get<Me>('/auth/me')
      setUser(data)
      setStatus('authenticated')
      return data
    } catch {
      clearSession()
      return null
    }
  }, [clearSession])

  useEffect(() => {
    void fetchMe()
  }, [fetchMe])

  useEffect(() => {
    const handleUnauthorized = () => clearSession()
    window.addEventListener('vaultrag:unauthorized', handleUnauthorized)
    return () =>
      window.removeEventListener('vaultrag:unauthorized', handleUnauthorized)
  }, [clearSession])

  const login = useCallback(async (payload: LoginRequest): Promise<Me> => {
    const { data: tokenResponse } = await api.post<TokenResponse>(
      '/auth/login',
      payload,
    )
    storeToken(tokenResponse.access_token)
    try {
      const { data } = await api.get<Me>('/auth/me')
      setUser(data)
      setStatus('authenticated')
      return data
    } catch (error) {
      clearStoredToken()
      setUser(null)
      setStatus('unauthenticated')
      throw error
    }
  }, [])

  const logout = useCallback(() => {
    clearSession()
  }, [clearSession])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      status,
      isAuthenticated: status === 'authenticated' && user !== null,
      login,
      logout,
      refreshMe: fetchMe,
    }),
    [user, status, login, logout, fetchMe],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider')
  }
  return context
}
