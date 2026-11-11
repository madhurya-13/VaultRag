import { expect, type Page } from '@playwright/test'

export const tenantId =
  process.env.E2E_TENANT_ID ?? ''

export const credentials = {
  admin: {
    email: process.env.E2E_ADMIN_EMAIL ?? '',
    password: process.env.E2E_ADMIN_PASSWORD ?? '',
  },
  employee: {
    email: process.env.E2E_EMPLOYEE_EMAIL ?? '',
    password: process.env.E2E_EMPLOYEE_PASSWORD ?? '',
  },
} as const

export async function signIn(
  page: Page,
  account: { email: string; password: string },
) {
  const accountKind = account === credentials.admin ? 'ADMIN' : 'EMPLOYEE'
  const missing = [
    !tenantId && 'E2E_TENANT_ID',
    !account.email && `E2E_${accountKind}_EMAIL`,
    !account.password && `E2E_${accountKind}_PASSWORD`,
  ].filter(Boolean)
  if (missing.length > 0) {
    throw new Error(`Set Playwright environment values: ${missing.join(', ')}`)
  }
  await page.goto('/login')
  await page.getByLabel('Organization ID').fill(tenantId)
  await page.getByLabel('Work email').fill(account.email)
  await page.getByLabel('Password').fill(account.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
}

export async function loginAsAdmin(page: Page) {
  await signIn(page, credentials.admin)
  await expect(page).toHaveURL(/\/dashboard$/)
  await expect(
    page.getByRole('heading', { name: /Good (morning|afternoon|evening)/ }),
  ).toBeVisible()
}

export async function loginAsEmployee(page: Page) {
  await signIn(page, credentials.employee)
  await expect(page).toHaveURL(/\/dashboard$/)
  await expect(
    page.getByRole('heading', { name: /Good (morning|afternoon|evening)/ }),
  ).toBeVisible()
}

export function workspaceNav(page: Page) {
  return page.getByRole('navigation', { name: 'Workspace' })
}

export function adminNav(page: Page) {
  return page.getByRole('navigation', { name: 'Administration' })
}
