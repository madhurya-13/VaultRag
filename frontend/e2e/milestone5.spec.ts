import { expect, test } from '@playwright/test'

import {
  adminNav,
  credentials,
  loginAsAdmin,
  loginAsEmployee,
  signIn,
  workspaceNav,
} from './helpers'

test.describe('authentication', () => {
  test('rejects invalid credentials with the backend error', async ({ page }) => {
    await signIn(page, {
      email: credentials.employee.email,
      password: 'definitely-not-the-password',
    })

    await expect(page.getByRole('alert')).toContainText(/invalid|credential/i)
    await expect(page).toHaveURL(/\/login$/)
  })

  test('admin signs in and receives administration navigation', async ({
    page,
  }) => {
    await loginAsAdmin(page)

    await expect(adminNav(page).getByRole('link', { name: 'Users & roles' })).toBeVisible()
    await expect(adminNav(page).getByRole('link', { name: 'Security' })).toBeVisible()
    await expect(workspaceNav(page).getByRole('link', { name: 'Documents' })).toBeVisible()
  })

  test('employee navigation omits admin routes and direct URLs are forbidden', async ({
    page,
  }) => {
    await loginAsEmployee(page)

    await expect(adminNav(page)).toHaveCount(0)

    await page.goto('/admin/users')
    await expect(page).toHaveURL(/\/forbidden$/)
    await expect(
      page.getByRole('heading', {
        name: 'This view requires different permissions.',
      }),
    ).toBeVisible()
  })
})

test.describe('documents', () => {
  test('employee sees authorized inventory without administrative actions', async ({
    page,
  }) => {
    await loginAsEmployee(page)
    await workspaceNav(page).getByRole('link', { name: 'Documents' }).click()
    await expect(page).toHaveURL(/\/documents$/)

    await expect(
      page.getByText('Information Security Policy 2026').first(),
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: /upload pdf/i }),
    ).toHaveCount(0)

    await page
      .getByRole('button', { name: 'Information Security Policy 2026' })
      .click()
    const detailDialog = page.getByRole('dialog', {
      name: 'Information Security Policy 2026',
    })
    await expect(
      detailDialog.getByRole('heading', {
        name: 'Information Security Policy 2026',
      }),
    ).toBeVisible()
    await expect(
      detailDialog.getByRole('button', { name: 'Manage access' }),
    ).toHaveCount(0)
  })

  test('admin can open the document access grant view', async ({ page }) => {
    await loginAsAdmin(page)
    await workspaceNav(page).getByRole('link', { name: 'Documents' }).click()

    await page
      .getByRole('button', { name: 'Information Security Policy 2026' })
      .click()
    const detailDialog = page.getByRole('dialog', {
      name: 'Information Security Policy 2026',
    })
    await detailDialog.getByRole('button', { name: 'Manage access' }).click()

    const permissionsDialog = page.getByRole('dialog', {
      name: 'Document access',
    })
    await expect(
      permissionsDialog.getByRole('heading', { name: 'Document access' }),
    ).toBeVisible()
    await expect(permissionsDialog.getByText('Grant access')).toBeVisible()
    await expect(permissionsDialog.getByLabel('Subject type')).toBeVisible()
    await expect(permissionsDialog.getByLabel('Action')).toBeVisible()
  })
})

test.describe('administration', () => {
  test('role catalogue and capability matrix load from the real API', async ({
    page,
  }) => {
    await loginAsAdmin(page)
    await adminNav(page).getByRole('link', { name: 'Users & roles' }).click()
    await expect(page).toHaveURL(/\/admin\/users$/)

    await page.getByRole('tab', { name: 'Roles & permissions' }).click()

    await expect(
      page.getByRole('heading', { name: 'Capability matrix' }),
    ).toBeVisible()
    await expect(page.getByText('Administrator').first()).toBeVisible()
    await expect(page.getByText(/Code-defined tenant capabilities/i)).toBeVisible()
  })

  test('security analytics render and audit filters open event detail', async ({
    page,
  }) => {
    await loginAsAdmin(page)
    await adminNav(page).getByRole('link', { name: 'Security' }).click()
    await expect(page).toHaveURL(/\/security$/)

    await expect(page.getByText('Audit events')).toBeVisible()
    await expect(page.locator('svg.recharts-surface')).toHaveCount(3)

    await page.getByRole('tab', { name: 'Audit log' }).click()
    await expect(
      page.getByRole('heading', { name: 'Audit trail' }),
    ).toBeVisible()

    await page.locator('#audit-decision').selectOption('DENY')
    await expect(page.getByRole('cell', { name: 'DENY' }).first()).toBeVisible()

    await page
      .getByRole('button', { name: 'Open audit event detail' })
      .first()
      .click()
    const dialog = page.getByRole('dialog', { name: 'Audit event detail' })
    await expect(
      dialog.getByRole('heading', { name: 'Audit event detail' }),
    ).toBeVisible()
    await expect(dialog.getByText('Timestamp')).toBeVisible()
    await expect(dialog.getByText('Request ID')).toBeVisible()
  })
})

test.describe('assistant', () => {
  test('sends a real RAG question and renders the grounded answer or backend error', async ({
    page,
  }) => {
    test.setTimeout(120_000)
    await loginAsEmployee(page)
    await workspaceNav(page).getByRole('link', { name: 'AI Assistant' }).click()
    await expect(page).toHaveURL(/\/assistant$/)

    const responsePromise = page.waitForResponse(
      (response) =>
        response.url().endsWith('/rag/ask') &&
        response.request().method() === 'POST',
      { timeout: 120_000 },
    )

    await page
      .getByRole('textbox', { name: 'Question' })
      .fill('What does the information security policy require?')
    await page.getByRole('button', { name: 'Send question' }).click()

    const response = await responsePromise
    if (response.ok()) {
      const answer = await response.json()
      expect(answer.answer).toBeTruthy()
      await expect(page.getByText(answer.answer)).toBeVisible({
        timeout: 60_000,
      })
      if (answer.sources.length > 0) {
        await expect(page.getByText(/validated citation/i)).toBeVisible()
        await expect(
          page.getByRole('link', { name: /Information Security Policy 2026/i }),
        ).toBeVisible()
      } else {
        await expect(
          page.getByText('No validated document citations were returned for this answer.'),
        ).toBeVisible()
      }
    } else {
      expect(response.status()).toBeGreaterThanOrEqual(500)
      const body = await response.json()
      expect(typeof body.detail).toBe('string')
      await expect(page.getByText(body.detail)).toBeVisible({
        timeout: 60_000,
      })
    }
  })
})

test.describe('mobile navigation', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('employee opens the off-canvas sidebar and navigates', async ({ page }) => {
    await loginAsEmployee(page)

    const sidebar = page.getByRole('complementary', {
      name: 'Primary navigation',
    })
    await expect(sidebar).not.toBeInViewport()

    await page.getByRole('button', { name: 'Open navigation' }).click()
    await expect(sidebar).toBeInViewport()

    await sidebar.getByRole('link', { name: 'Documents' }).click()
    await expect(page).toHaveURL(/\/documents$/)
    await expect(sidebar).not.toBeInViewport()
  })
})
