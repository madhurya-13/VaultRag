import axios from 'axios'
import type { ApiErrorBody } from '@/types/api'

export const TOKEN_KEY = 'vaultrag.access_token'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  headers: { Accept: 'application/json' },
  timeout: 90_000,
})

export function getStoredToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function storeToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token)
}

export function clearStoredToken(): void {
  window.localStorage.removeItem(TOKEN_KEY)
}

api.interceptors.request.use((config) => {
  const token = getStoredToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (
      axios.isAxiosError(error) &&
      error.response?.status === 401 &&
      getStoredToken()
    ) {
      clearStoredToken()
      window.dispatchEvent(new Event('vaultrag:unauthorized'))
    }
    return Promise.reject(error)
  },
)

export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError<ApiErrorBody>(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((item) => item.msg)
        .filter((message): message is string => Boolean(message))
        .join(' ')
    }
    if (error.code === 'ECONNABORTED') {
      return 'The request timed out. The AI answer may still be generating; try again.'
    }
    if (!error.response) {
      return 'The API is unreachable. Check that the FastAPI backend is running.'
    }
    return `Request failed with status ${error.response.status}.`
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'Something went wrong. Please try again.'
}
