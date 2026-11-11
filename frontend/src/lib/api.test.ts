import { AxiosError } from 'axios'
import { describe, expect, it } from 'vitest'

import { getErrorMessage, TOKEN_KEY } from '@/lib/api'

function responseError(status: number, data: unknown): AxiosError {
  const error = new AxiosError('Request failed')
  error.response = {
    data,
    status,
    statusText: '',
    headers: {},
    config: {},
  } as never
  return error
}

describe('getErrorMessage', () => {
  it('uses a string detail returned by the API', () => {
    expect(
      getErrorMessage(responseError(403, { detail: 'Not authorized' })),
    ).toBe('Not authorized')
  })

  it('joins FastAPI validation messages', () => {
    expect(
      getErrorMessage(
        responseError(422, {
          detail: [{ msg: 'tenant_id must be a UUID' }, { msg: 'email is invalid' }],
        }),
      ),
    ).toBe('tenant_id must be a UUID email is invalid')
  })

  it('explains timeouts without discarding the backend state', () => {
    const error = new AxiosError('timeout', 'ECONNABORTED')
    expect(getErrorMessage(error)).toMatch(/timed out/i)
  })

  it('explains an unreachable API', () => {
    expect(getErrorMessage(new AxiosError('Network Error'))).toMatch(
      /API is unreachable/i,
    )
  })

  it('falls back to the HTTP status when no detail is present', () => {
    expect(getErrorMessage(responseError(500, {}))).toBe(
      'Request failed with status 500.',
    )
  })

  it('uses plain Error messages and a final fallback', () => {
    expect(getErrorMessage(new Error('Boom'))).toBe('Boom')
    expect(getErrorMessage('unexpected')).toBe(
      'Something went wrong. Please try again.',
    )
  })
})

describe('token storage contract', () => {
  it('uses a namespaced localStorage key', () => {
    expect(TOKEN_KEY).toBe('vaultrag.access_token')
  })
})
