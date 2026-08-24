/**
 * Admin API helpers — mirror services/admin-service/app/api/routes/admin.py.
 * Uses the shared in-memory access token (via getAccessToken) for Authorization.
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

// The backend client exposes `client` privately; re-create a thin wrapper that
// reuses the same baseURL + interceptor behavior via a fresh axios instance.
import axios from 'axios';
import { getAccessToken } from './client';

// Same host-derived base as the main client so LAN-IP browsing does not
// send admin calls to localhost (CORS failure).
import { API_BASE_URL as baseURL } from './client';

function authHeaders() {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function api() {
  return axios.create({ baseURL, timeout: 15000 });
}

// ---- User moderation ----
export async function listUsers(params: ListParams & { status?: string; search?: string } = {}) {
  // Backend: GET /api/admin/users/moderated?status=&limit=&offset=
  // We also support a client-side `search` filter (applied in the UI) since the
  // backend endpoint does not expose a query param for it.
  const { data } = await api().get<AdminUser[]>('/admin/api/v1/admin/users/moderated', {
    params: { limit: params.limit ?? 50, offset: params.offset ?? 0, status: params.status || undefined },
    headers: authHeaders(),
  });
  return data;
}

export async function moderateUser(user_id: string, status: UserStatus, reason?: string) {
  const { data } = await api().post('/admin/api/v1/admin/users/moderate', {
    user_id,
    status,
    reason,
  }, { headers: authHeaders() });
  return data;
}

// ---- Content flags ----
export async function listFlags(params: ListParams = {}) {
  const { data } = await api().get<ContentFlag[]>('/admin/api/v1/admin/content/flagged', {
    params: { limit: params.limit ?? 50, offset: params.offset ?? 0 },
    headers: authHeaders(),
  });
  return data;
}

export async function resolveFlag(content_id: string, status: ContentStatus) {
  const { data } = await api().post('/admin/api/v1/admin/content/resolve', null, {
    params: { content_id, status },
    headers: authHeaders(),
  });
  return data;
}

// ---- System alerts ----
export async function listAlerts(params: ListParams = {}) {
  const { data } = await api().get<SystemAlert[]>('/admin/api/v1/admin/alerts', {
    params: { limit: params.limit ?? 50 },
    headers: authHeaders(),
  });
  return data;
}

export async function createAlert(input: {
  alert_type: string;
  severity: AlertSeverity;
  message: string;
  service: string;
}) {
  const { data } = await api().post('/admin/api/v1/admin/alerts', input, {
    headers: authHeaders(),
  });
  return data;
}

export async function acknowledgeAlert(alert_id: number) {
  const { data } = await api().post(`/admin/api/v1/admin/alerts/${alert_id}/acknowledge`, null, {
    headers: authHeaders(),
  });
  return data;
}

// ---- System config ----
export async function listConfigs(params: ListParams = {}) {
  const { data } = await api().get<SystemConfig[]>('/admin/api/v1/admin/config', {
    params: { limit: params.limit ?? 100 },
    headers: authHeaders(),
  });
  return data;
}

export async function getConfig(key: string) {
  const { data } = await api().get<SystemConfig>(`/admin/api/v1/admin/config/${key}`, {
    headers: authHeaders(),
  });
  return data;
}

export async function setConfig(input: {
  key: string;
  value: string;
  config_type: ConfigType;
  description?: string;
}) {
  const { data } = await api().post('/admin/api/v1/admin/config', input, {
    headers: authHeaders(),
  });
  return data;
}

// ---- Audit log ----
export async function listAuditLogs(params: { admin_id?: string; resource_type?: string; resource_id?: string; limit?: number } = {}) {
  const { admin_id, resource_type, resource_id, limit = 50 } = params;
  if (admin_id) {
    const { data } = await api().get<AuditLog[]>(`/admin/api/v1/admin/audit/admin/${admin_id}`, {
      params: { limit },
      headers: authHeaders(),
    });
    return data;
  }
  if (resource_type && resource_id) {
    const { data } = await api().get<AuditLog[]>(
      `/admin/api/v1/admin/audit/resource/${resource_type}/${resource_id}`,
      { params: { limit }, headers: authHeaders() },
    );
    return data;
  }
  return [] as AuditLog[];
}

// ---- System stats ----
export async function getSystemStats() {
  const { data } = await api().get<SystemStats>('/admin/api/v1/admin/stats', {
    headers: authHeaders(),
  });
  return data;
}
