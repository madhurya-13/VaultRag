import { expect, test } from '@playwright/test'

import { adminNav, loginAsAdmin, loginAsEmployee, workspaceNav } from './helpers'

test.describe('Milestone 10 administrative surfaces', () => {
  test('admin can open feedback, gaps, analytics and settings', async ({ page }) => {
    await loginAsAdmin(page)
    for (const [label, heading] of [
      ['Feedback', /Feedback review/i],
      ['Knowledge gaps', /Knowledge gaps/i],
      ['Analytics', /^Analytics$/i],
      ['Settings', /Tenant settings/i],
    ] as const) {
      await adminNav(page).getByRole('link', { name: label }).click()
      await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible()
    }
  })

  test('evaluation remains reachable from administration navigation', async ({ page }) => {
    await loginAsAdmin(page)
    await adminNav(page).getByRole('link', { name: 'RAG evaluation' }).click()
    await expect(page.getByRole('heading', { name: 'Evaluation' })).toBeVisible()
  })

  test('employee never sees administrative surfaces', async ({ page }) => {
    await loginAsEmployee(page)
    await expect(adminNav(page)).toHaveCount(0)
    await expect(workspaceNav(page).getByRole('link', { name: 'AI Assistant' })).toBeVisible()
    await page.goto('/admin/settings')
    await expect(page).toHaveURL(/\/forbidden$/)
  })
})
