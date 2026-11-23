import { lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import {
  CapabilityRoute,
  ProtectedRoute,
} from '@/components/auth/protected-route'
import { AppShell } from '@/components/layout/app-shell'
import { CAP } from '@/lib/capabilities'
import { ForbiddenPage } from '@/pages/forbidden-page'
import { LoginPage } from '@/pages/login-page'
import { NotFoundPage } from '@/pages/not-found-page'

const ChatPage = lazy(() =>
  import('@/pages/chat-page').then((module) => ({
    default: module.ChatPage,
  })),
)
const DashboardPage = lazy(() =>
  import('@/pages/dashboard-page').then((module) => ({
    default: module.DashboardPage,
  })),
)
const DocumentsPage = lazy(() =>
  import('@/pages/documents-page').then((module) => ({
    default: module.DocumentsPage,
  })),
)
const EvaluationPage = lazy(() =>
  import('@/pages/evaluation-page').then((module) => ({
    default: module.EvaluationPage,
  })),
)
const AnalyticsPage = lazy(() =>
  import('@/pages/analytics-page').then((module) => ({
    default: module.AnalyticsPage,
  })),
)
const FeedbackPage = lazy(() =>
  import('@/pages/feedback-page').then((module) => ({
    default: module.FeedbackPage,
  })),
)
const KnowledgeGapsPage = lazy(() =>
  import('@/pages/knowledge-gaps-page').then((module) => ({
    default: module.KnowledgeGapsPage,
  })),
)
const SettingsPage = lazy(() =>
  import('@/pages/settings-page').then((module) => ({
    default: module.SettingsPage,
  })),
)
const IntegrationsPage = lazy(() =>
  import('@/pages/integrations-page').then((module) => ({
    default: module.IntegrationsPage,
  })),
)
const GraphPage = lazy(() =>
  import('@/pages/graph-page').then((module) => ({
    default: module.GraphPage,
  })),
)
const SecurityPage = lazy(() =>
  import('@/pages/security-page').then((module) => ({
    default: module.SecurityPage,
  })),
)
const UsersPage = lazy(() =>
  import('@/pages/users-page').then((module) => ({
    default: module.UsersPage,
  })),
)

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="assistant" element={<ChatPage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="graph" element={<GraphPage />} />

          <Route
            element={
              <CapabilityRoute
                anyOf={[CAP.MANAGE_USERS, CAP.MANAGE_ROLES]}
              />
            }
          >
            <Route path="admin/users" element={<UsersPage />} />
          </Route>

          <Route
            element={<CapabilityRoute capability={CAP.MANAGE_INTEGRATIONS} />}
          >
            <Route path="admin/integrations" element={<IntegrationsPage />} />
          </Route>

          <Route
            element={<CapabilityRoute capability={CAP.MANAGE_EVALUATION} />}
          >
            <Route path="admin/evaluation" element={<EvaluationPage />} />
          </Route>

          <Route
            element={<CapabilityRoute capability={CAP.VIEW_FEEDBACK} />}
          >
            <Route path="admin/feedback" element={<FeedbackPage />} />
          </Route>

          <Route
            element={<CapabilityRoute capability={CAP.MANAGE_KNOWLEDGE_GAPS} />}
          >
            <Route path="admin/knowledge-gaps" element={<KnowledgeGapsPage />} />
          </Route>

          <Route
            element={<CapabilityRoute capability={CAP.VIEW_ANALYTICS} />}
          >
            <Route path="admin/analytics" element={<AnalyticsPage />} />
          </Route>

          <Route
            element={<CapabilityRoute capability={CAP.MANAGE_TENANT_SETTINGS} />}
          >
            <Route path="admin/settings" element={<SettingsPage />} />
          </Route>

          <Route
            element={<CapabilityRoute capability={CAP.VIEW_AUDIT_LOGS} />}
          >
            <Route path="security" element={<SecurityPage />} />
          </Route>

          <Route path="forbidden" element={<ForbiddenPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  )
}
