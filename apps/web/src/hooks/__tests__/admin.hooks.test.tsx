/**
 * Tests for the admin React Query hooks.
 *
 * Each test builds its own QueryClient with `retry: false` (the app singleton
 * retries twice, which would make every failure assertion slow and flaky) and
 * never touches the shared instance in `@/utils/queryClient`.
 *
 * The behaviours that matter: a failed query surfaces its error instead of
 * hanging, a successful mutation refreshes the list it changed, and the toast
 * copy picks the most specific message available.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listUsers: vi.fn(),
  moderateUser: vi.fn(),
  listFlags: vi.fn(),
  resolveFlag: vi.fn(),
  listAlerts: vi.fn(),
  createAlert: vi.fn(),
  acknowledgeAlert: vi.fn(),
  listConfigs: vi.fn(),
  setConfig: vi.fn(),
  listAuditLogs: vi.fn(),
  getSystemStats: vi.fn(),
}));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock('@/api/admin', () => api);
vi.mock('sonner', () => ({ toast }));

import {
  useAcknowledgeAlert,
  useAlerts,
  useAuditLogs,
  useConfigs,
  useCreateAlert,
  useFlags,
  useModerateUser,
  useResolveFlag,
  useSetConfig,
  useUsers,
} from '@/hooks/admin';

let queryClient: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return createElement(QueryClientProvider, { client: queryClient }, children);
}

function mount<T, P = undefined>(hook: (props: P) => T, initialProps?: P) {
  return renderHook(hook, { wrapper, initialProps: initialProps as P });
}

/** Stand-in for an axios error carrying a FastAPI `detail` string. */
function httpError(detail: string, status = 400): Error {
  const error = new Error('Request failed') as Error & {
    response: { status: number; data: { detail: string } };
  };
  error.response = { status, data: { detail } };
  return error;
}

/** Read the status off a rejected mutation/query error without a blind cast. */
function statusOf(error: unknown): number | undefined {
  return (error as { response?: { status?: number } }).response?.status;
}

/** Read the FastAPI `detail` off a rejected error without a blind cast. */
function detailOf(error: unknown): string | undefined {
  return (error as { response?: { data?: { detail?: string } } }).response?.data?.detail;
}

beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  for (const fn of Object.values(api)) fn.mockReset();
  toast.success.mockReset();
  toast.error.mockReset();
});

afterEach(() => {
  queryClient.clear();
});

describe('useUsers', () => {
  it('resolves with the moderated-user list', async () => {
    api.listUsers.mockResolvedValue([{ id: 'u1', status: 'active' }]);

    const { result } = mount(() => useUsers({ status: 'suspended' }));

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ id: 'u1', status: 'active' }]);
    expect(api.listUsers).toHaveBeenCalledWith({ status: 'suspended' });
  });

  it('calls the API with no arguments by default', async () => {
    api.listUsers.mockResolvedValue([]);

    const { result } = mount(() => useUsers());

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.listUsers).toHaveBeenCalledWith({});
  });

  it('surfaces the failure instead of retrying forever', async () => {
    api.listUsers.mockRejectedValue(httpError('admin-service unreachable', 503));

    const { result } = mount(() => useUsers());

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(api.listUsers).toHaveBeenCalledTimes(1);
    expect(statusOf(result.current.error)).toBe(503);
  });

  it('is pending before the first response arrives', () => {
    api.listUsers.mockReturnValue(new Promise(() => {}));

    const { result } = mount(() => useUsers());

    expect(result.current.isPending).toBe(true);
    expect(result.current.data).toBeUndefined();
  });

  it('fetches once per distinct param object', async () => {
    api.listUsers.mockResolvedValue([]);
    const params = { limit: 10, offset: 0 };

    const { rerender } = mount(() => useUsers(params));
    await waitFor(() => expect(api.listUsers).toHaveBeenCalledTimes(1));

    rerender();
    rerender();

    await new Promise((r) => setTimeout(r, 0));
    expect(api.listUsers).toHaveBeenCalledTimes(1);
  });

  it('refetches when a filter changes', async () => {
    api.listUsers.mockResolvedValue([]);
    const { result, rerender } = mount(
      ({ status }: { status?: string }) => useUsers({ status }),
      { status: 'active' }
    );

    await waitFor(() => expect(api.listUsers).toHaveBeenCalledTimes(1));
    rerender({ status: 'banned' });

    await waitFor(() => expect(api.listUsers).toHaveBeenCalledTimes(2));
    expect(api.listUsers).toHaveBeenLastCalledWith({ status: 'banned' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.listUsers).toHaveBeenLastCalledWith({ status: 'banned' });
  });
});

describe('useModerateUser', () => {
  it('sends the moderation request and refreshes the users list', async () => {
    api.listUsers.mockResolvedValue([]);
    api.moderateUser.mockResolvedValue({ status: 'banned' });

    const { result } = mount(() => ({
      users: useUsers(),
      moderate: useModerateUser(),
    }));

    await waitFor(() => expect(result.current.users.isSuccess).toBe(true));
    await result.current.moderate.mutateAsync({ user_id: 'u1', status: 'banned', reason: 'fraud' });

    expect(api.moderateUser).toHaveBeenCalledWith('u1', 'banned', 'fraud');
    // The moderation table must reflect the new status without a manual reload.
    await waitFor(() => expect(api.listUsers).toHaveBeenCalledTimes(2));
  });

  it('confirms with the new status in the toast', async () => {
    api.moderateUser.mockResolvedValue({});
    const { result } = mount(() => useModerateUser());

    await result.current.mutateAsync({ user_id: 'u1', status: 'suspended' });

    expect(toast.success).toHaveBeenCalledWith('User suspended');
  });

  it('toasts the backend detail when the mutation fails', async () => {
    api.moderateUser.mockRejectedValue(httpError('You cannot ban yourself'));
    const { result } = mount(() => useModerateUser());

    await expect(
      result.current.mutateAsync({ user_id: 'u1', status: 'banned' })
    ).rejects.toBeDefined();

    // The backend's reason is far more actionable than the generic fallback.
    expect(toast.error).toHaveBeenCalledWith('You cannot ban yourself');
  });

  it('falls back to the error message when the body has no detail', async () => {
    api.moderateUser.mockRejectedValue({ message: 'Network Error' });
    const { result } = mount(() => useModerateUser());

    await expect(result.current.mutateAsync({ user_id: 'u1', status: 'banned' })).rejects.toBeDefined();

    expect(toast.error).toHaveBeenCalledWith('Network Error');
  });

  it('falls back to the generic copy for an opaque error', async () => {
    api.moderateUser.mockRejectedValue({});
    const { result } = mount(() => useModerateUser());

    await expect(result.current.mutateAsync({ user_id: 'u1', status: 'banned' })).rejects.toBeDefined();

    expect(toast.error).toHaveBeenCalledWith('Failed to moderate user');
  });

  it('does not confirm when the mutation fails', async () => {
    api.moderateUser.mockRejectedValue(httpError('nope'));
    const { result } = mount(() => useModerateUser());

    await expect(result.current.mutateAsync({ user_id: 'u1', status: 'banned' })).rejects.toBeDefined();

    expect(toast.success).not.toHaveBeenCalled();
  });

  it('exposes pending state while the request is in flight', async () => {
    let release: (v: unknown) => void = () => {};
    api.moderateUser.mockReturnValue(new Promise((r) => (release = r)));
    const { result } = mount(() => useModerateUser());

    const pending = result.current.mutate({ user_id: 'u1', status: 'banned' });

    await waitFor(() => expect(result.current.isPending).toBe(true));
    // While in flight the admin must be able to tell the request is running.
    expect(result.current.isIdle).toBe(false);

    release({});
    await pending;

    await waitFor(() => expect(result.current.isPending).toBe(false));
    expect(result.current.isSuccess).toBe(true);
  });
});

describe('useFlags and useResolveFlag', () => {
  it('resolves the flagged-content list', async () => {
    api.listFlags.mockResolvedValue([{ id: 1, content_id: 'c1' }]);

    const { result } = mount(() => useFlags({ limit: 25 }));

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ id: 1, content_id: 'c1' }]);
    expect(api.listFlags).toHaveBeenCalledWith({ limit: 25 });
  });

  it('refreshes the flags list after a resolution', async () => {
    api.listFlags.mockResolvedValue([]);
    api.resolveFlag.mockResolvedValue({ status: 'removed' });

    const { result } = mount(() => ({ flags: useFlags(), resolve: useResolveFlag() }));
    await waitFor(() => expect(result.current.flags.isSuccess).toBe(true));

    await result.current.resolve.mutateAsync({ content_id: 'c1', status: 'removed' });

    expect(api.resolveFlag).toHaveBeenCalledWith('c1', 'removed');
    await waitFor(() => expect(api.listFlags).toHaveBeenCalledTimes(2));
  });

  it('confirms a resolution and reports backend failures', async () => {
    api.resolveFlag.mockResolvedValue({});
    const ok = mount(() => useResolveFlag());
    await ok.result.current.mutateAsync({ content_id: 'c1', status: 'active' });
    expect(toast.success).toHaveBeenCalledWith('Flag updated');

    toast.error.mockReset();
    api.resolveFlag.mockRejectedValue(httpError('flag already resolved', 409));
    const bad = mount(() => useResolveFlag());
    await expect(
      bad.result.current.mutateAsync({ content_id: 'c1', status: 'active' })
    ).rejects.toBeDefined();
    expect(toast.error).toHaveBeenCalledWith('flag already resolved');
  });
});

describe('useAlerts, useCreateAlert and useAcknowledgeAlert', () => {
  it('resolves the alerts list', async () => {
    api.listAlerts.mockResolvedValue([{ id: 3, severity: 'critical' }]);

    const { result } = mount(() => useAlerts({ limit: 10 }));

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.listAlerts).toHaveBeenCalledWith({ limit: 10 });
  });

  it('refreshes the alerts list after creating one', async () => {
    api.listAlerts.mockResolvedValue([]);
    api.createAlert.mockResolvedValue({ id: 4 });

    const { result } = mount(() => ({ alerts: useAlerts(), create: useCreateAlert() }));
    await waitFor(() => expect(result.current.alerts.isSuccess).toBe(true));

    await result.current.create.mutateAsync({
      alert_type: 'disk_pressure',
      severity: 'warning',
      message: 'content-service at 88%',
      service: 'content-service',
    });

    expect(api.createAlert).toHaveBeenCalledWith({
      alert_type: 'disk_pressure',
      severity: 'warning',
      message: 'content-service at 88%',
      service: 'content-service',
    });
    expect(toast.success).toHaveBeenCalledWith('Alert created');
    await waitFor(() => expect(api.listAlerts).toHaveBeenCalledTimes(2));
  });

  it('refreshes the alerts list after acknowledging one', async () => {
    api.listAlerts.mockResolvedValue([]);
    api.acknowledgeAlert.mockResolvedValue({ acknowledged: true });

    const { result } = mount(() => ({ alerts: useAlerts(), ack: useAcknowledgeAlert() }));
    await waitFor(() => expect(result.current.alerts.isSuccess).toBe(true));

    await result.current.ack.mutateAsync(7);

    expect(api.acknowledgeAlert).toHaveBeenCalledWith(7);
    expect(toast.success).toHaveBeenCalledWith('Alert acknowledged');
    await waitFor(() => expect(api.listAlerts).toHaveBeenCalledTimes(2));
  });

  it('reports an acknowledgement failure with the fallback copy', async () => {
    api.acknowledgeAlert.mockRejectedValue({});
    const { result } = mount(() => useAcknowledgeAlert());

    await expect(result.current.mutateAsync(7)).rejects.toBeDefined();

    expect(toast.error).toHaveBeenCalledWith('Failed to acknowledge');
  });
});

describe('useConfigs and useSetConfig', () => {
  it('resolves the config list', async () => {
    api.listConfigs.mockResolvedValue([{ key: 'SIGNUP_ENABLED', value: 'true' }]);

    const { result } = mount(() => useConfigs({ limit: 200 }));

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.listConfigs).toHaveBeenCalledWith({ limit: 200 });
  });

  it('refreshes the config list after a write and names the key', async () => {
    api.listConfigs.mockResolvedValue([]);
    api.setConfig.mockResolvedValue({ key: 'MAX_STREAMS' });

    const { result } = mount(() => ({ configs: useConfigs(), save: useSetConfig() }));
    await waitFor(() => expect(result.current.configs.isSuccess).toBe(true));

    await result.current.save.mutateAsync({ key: 'MAX_STREAMS', value: '4', config_type: 'integer' });

    expect(api.setConfig).toHaveBeenCalledWith({ key: 'MAX_STREAMS', value: '4', config_type: 'integer' });
    expect(toast.success).toHaveBeenCalledWith('Config "MAX_STREAMS" saved');
    await waitFor(() => expect(api.listConfigs).toHaveBeenCalledTimes(2));
  });

  it('reports a config write failure', async () => {
    api.setConfig.mockRejectedValue(httpError('key is read-only'));
    const { result } = mount(() => useSetConfig());

    await expect(
      result.current.mutateAsync({ key: 'JWT_SECRET', value: 'x', config_type: 'string' })
    ).rejects.toBeDefined();

    expect(toast.error).toHaveBeenCalledWith('key is read-only');
  });
});

describe('useAuditLogs', () => {
  it('resolves the audit trail for a resource', async () => {
    api.listAuditLogs.mockResolvedValue([{ id: 1, action: 'ban' }]);

    const { result } = mount(() => useAuditLogs({ resource_type: 'content', resource_id: 'c1' }));

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ id: 1, action: 'ban' }]);
    expect(api.listAuditLogs).toHaveBeenCalledWith({ resource_type: 'content', resource_id: 'c1' });
  });

  it('resolves to an empty list when no filter is supplied', async () => {
    api.listAuditLogs.mockResolvedValue([]);

    const { result } = mount(() => useAuditLogs());

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it('surfaces an audit-log failure', async () => {
    api.listAuditLogs.mockRejectedValue(httpError('audit index rebuilding', 503));

    const { result } = mount(() => useAuditLogs({ admin_id: 'a1' }));

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(detailOf(result.current.error)).toBe('audit index rebuilding');
  });
});
