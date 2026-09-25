/**
 * Admin API helpers — mirror services/admin-service/app/api/routes/admin.py.
 * Uses the shared authenticated transport and single-flight token refresh.
 */
import type {
  AdminUser,
  ContentFlag,
  SystemAlert,
  SystemConfig,
  AuditLog,
  SystemStats,
  UserStatus,
  ContentStatus,
  AlertSeverity,
  ConfigType,
} from '@/types/admin';

export interface ListParams {
  limit?: number;
  offset?: number;
}

import { apiClient } from './client';

// ---- User moderation ----
export async function listUsers(params: ListParams & { status?: string; search?: string } = {}) {
  // Backend: GET /api/admin/users/moderated?status=&limit=&offset=
  // We also support a client-side `search` filter (applied in the UI) since the
  // backend endpoint does not expose a query param for it.
  const { data } = await apiClient.client.get<AdminUser[]>('/admin/api/v1/admin/users/moderated', {
    params: { limit: params.limit ?? 50, offset: params.offset ?? 0, status: params.status || undefined },
  });
  return data;
}

export async function moderateUser(user_id: string, status: UserStatus, reason?: string) {
  const { data } = await apiClient.client.post('/admin/api/v1/admin/users/moderate', {
    user_id,
    status,
    reason,
  });
  return data;
}

// ---- Content flags ----
export async function listFlags(params: ListParams = {}) {
  const { data } = await apiClient.client.get<ContentFlag[]>('/admin/api/v1/admin/content/flagged', {
    params: { limit: params.limit ?? 50, offset: params.offset ?? 0 },
  });
  return data;
}

export async function resolveFlag(content_id: string, status: ContentStatus) {
  const { data } = await apiClient.client.post('/admin/api/v1/admin/content/resolve', null, {
    params: { content_id, status },
  });
  return data;
}

// ---- System alerts ----
export async function listAlerts(params: ListParams = {}) {
  const { data } = await apiClient.client.get<SystemAlert[]>('/admin/api/v1/admin/alerts', {
    params: { limit: params.limit ?? 50 },
  });
  return data;
}

export async function createAlert(input: {
  alert_type: string;
  severity: AlertSeverity;
  message: string;
  service: string;
}) {
  const { data } = await apiClient.client.post('/admin/api/v1/admin/alerts', input);
  return data;
}

export async function acknowledgeAlert(alert_id: number) {
  const { data } = await apiClient.client.post(`/admin/api/v1/admin/alerts/${alert_id}/acknowledge`);
  return data;
}

// ---- System config ----
export async function listConfigs(params: ListParams = {}) {
  const { data } = await apiClient.client.get<SystemConfig[]>('/admin/api/v1/admin/config', {
    params: { limit: params.limit ?? 100 },
  });
  return data;
}

export async function getConfig(key: string) {
  const { data } = await apiClient.client.get<SystemConfig>(`/admin/api/v1/admin/config/${key}`);
  return data;
}

export async function setConfig(input: {
  key: string;
  value: string;
  config_type: ConfigType;
  description?: string;
}) {
  const { data } = await apiClient.client.post('/admin/api/v1/admin/config', input);
  return data;
}

// ---- Audit log ----
export async function listAuditLogs(params: { admin_id?: string; resource_type?: string; resource_id?: string; limit?: number } = {}) {
  const { admin_id, resource_type, resource_id, limit = 50 } = params;
  if (admin_id) {
    const { data } = await apiClient.client.get<AuditLog[]>(`/admin/api/v1/admin/audit/admin/${admin_id}`, {
      params: { limit },
    });
    return data;
  }
  if (resource_type && resource_id) {
    const { data } = await apiClient.client.get<AuditLog[]>(
      `/admin/api/v1/admin/audit/resource/${resource_type}/${resource_id}`,
      { params: { limit } },
    );
    return data;
  }
  return [] as AuditLog[];
}

// ---- System stats ----
export async function getSystemStats() {
  const { data } = await apiClient.client.get<SystemStats>('/admin/api/v1/admin/stats');
  return data;
}
