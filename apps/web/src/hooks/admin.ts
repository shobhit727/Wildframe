/**
 * Admin React Query hooks — wrap the admin API helpers with caching + state.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/api/admin';
import type { UserStatus, ContentStatus, AlertSeverity, ConfigType } from '@/types/admin';

export function adminErrorMessage(e: unknown, fallback: string) {
  const anyE = e as { response?: { data?: { detail?: string } }; message?: string };
  return anyE?.response?.data?.detail ?? anyE?.message ?? fallback;
}

// ---- Step-up authentication ----
// Exchanges the admin password for a single-use token. Errors surface inline in
// the confirmation dialog, so no toast here.
export function useRequestStepUp() {
  return useMutation({
    mutationFn: (input: { password: string; mfa_code?: string }) =>
      api.requestStepUp(input.password, input.mfa_code),
  });
}

// ---- Users ----
export function useUsers(params: { limit?: number; offset?: number; status?: string; search?: string } = {}) {
  return useQuery({
    queryKey: ['admin', 'users', params],
    queryFn: () => api.listUsers(params),
    placeholderData: (prev) => prev,
  });
}

export function useModerateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { user_id: string; status: UserStatus; reason?: string; reauthToken?: string }) =>
      api.moderateUser(input.user_id, input.status, input.reason, input.reauthToken),
    onSuccess: (_, vars) => {
      toast.success(`User ${vars.status}`);
      qc.invalidateQueries({ queryKey: ['admin', 'users'] });
      qc.invalidateQueries({ queryKey: ['admin', 'stats'] });
    },
    onError: (e) => toast.error(adminErrorMessage(e, 'Failed to moderate user')),
  });
}

// ---- Flags ----
export function useFlags(params: { limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: ['admin', 'flags', params],
    queryFn: () => api.listFlags(params),
    placeholderData: (prev) => prev,
  });
}

export function useResolveFlag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { content_id: string; status: ContentStatus; reauthToken?: string }) =>
      api.resolveFlag(input.content_id, input.status, input.reauthToken),
    onSuccess: () => {
      toast.success('Flag updated');
      qc.invalidateQueries({ queryKey: ['admin', 'flags'] });
      qc.invalidateQueries({ queryKey: ['admin', 'stats'] });
    },
    onError: (e) => toast.error(adminErrorMessage(e, 'Failed to resolve flag')),
  });
}

// ---- Alerts ----
export function useAlerts(params: { limit?: number } = {}) {
  return useQuery({
    queryKey: ['admin', 'alerts', params],
    queryFn: () => api.listAlerts(params),
    placeholderData: (prev) => prev,
  });
}

export function useCreateAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { alert_type: string; severity: AlertSeverity; message: string; service: string }) =>
      api.createAlert(input),
    onSuccess: () => {
      toast.success('Alert created');
      qc.invalidateQueries({ queryKey: ['admin', 'alerts'] });
      qc.invalidateQueries({ queryKey: ['admin', 'stats'] });
    },
    onError: (e) => toast.error(adminErrorMessage(e, 'Failed to create alert')),
  });
}

export function useAcknowledgeAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (alert_id: number) => api.acknowledgeAlert(alert_id),
    onSuccess: () => {
      toast.success('Alert acknowledged');
      qc.invalidateQueries({ queryKey: ['admin', 'alerts'] });
      qc.invalidateQueries({ queryKey: ['admin', 'stats'] });
    },
    onError: (e) => toast.error(adminErrorMessage(e, 'Failed to acknowledge')),
  });
}

// ---- Config ----
export function useConfigs(params: { limit?: number } = {}) {
  return useQuery({
    queryKey: ['admin', 'configs', params],
    queryFn: () => api.listConfigs(params),
    placeholderData: (prev) => prev,
  });
}

export function useSetConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { key: string; value: string; config_type: ConfigType; description?: string }) =>
      api.setConfig(input),
    onSuccess: (_, vars) => {
      toast.success(`Config "${vars.key}" saved`);
      qc.invalidateQueries({ queryKey: ['admin', 'configs'] });
    },
    onError: (e) => toast.error(adminErrorMessage(e, 'Failed to save config')),
  });
}

// ---- Audit ----
export function useAuditLogs(params: { admin_id?: string; resource_type?: string; resource_id?: string; limit?: number } = {}) {
  return useQuery({
    queryKey: ['admin', 'audit', params],
    queryFn: () => api.listAuditLogs(params),
    placeholderData: (prev) => prev,
  });
}
