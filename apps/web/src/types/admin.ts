/**
 * Admin dashboard types — mirrored from services/admin-service/app/schemas/admin.py.
 * Backend admin endpoints are mounted under /api/admin.
 */

export type UserStatus = 'active' | 'suspended' | 'banned';
export type ContentStatus = 'active' | 'flagged' | 'removed';
export type ContentType = 'movie' | 'show' | 'episode';
export type AlertSeverity = 'info' | 'warning' | 'critical';
export type ConfigType = 'string' | 'integer' | 'boolean' | 'json';

export interface AdminUser {
  id: string;
  user_id: string;
  email: string;
  first_name: string;
  last_name: string;
  status: UserStatus | 'active' | 'suspended' | 'banned';
  reason?: string | null;
  moderated_by?: string | null;
  moderated_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ContentFlag {
  id: number;
  content_id: string;
  content_type: ContentType | string;
  status: ContentStatus | string;
  reason?: string | null;
  flagged_at?: string | null;
  resolved_at?: string | null;
  created_at: string;
}

export interface SystemAlert {
  id: number;
  alert_type: string;
  severity: AlertSeverity | string;
  message: string;
  service: string;
  acknowledged: boolean;
  acknowledged_by?: string | null;
  created_at: string;
}

export interface SystemConfig {
  id: number;
  key: string;
  value: string;
  config_type: ConfigType | string;
  description?: string | null;
  updated_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AuditLog {
  id: number;
  admin_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  changes?: string | null;
  ip_address?: string | null;
  created_at: string;
}

export interface SystemStats {
  total_users: number;
  active_users: number;
  suspended_users: number;
  flagged_content: number;
  active_alerts: number;
  system_uptime_hours: number;
}

export interface Paginated<T> {
  data: T[];
  total: number;
  limit: number;
  offset: number;
}
