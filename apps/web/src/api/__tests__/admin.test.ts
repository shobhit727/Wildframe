/**
 * Tests for the admin API helpers. These are thin wrappers, so the value under
 * test is the exact wire contract: route, query params vs body placement, and
 * default values. A wrong param name here silently filters nothing in the
 * admin UI, which is invisible until you look at the network tab.
 */
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('@/api/client', () => ({
  apiClient: { client: { get, post } },
}));

import * as admin from '@/api/admin';

type Params = Record<string, unknown> | undefined;

function ok(data: unknown) {
  return Promise.resolve({ data });
}

function boom(status = 500) {
  const config = { headers: new AxiosHeaders() };
  return Promise.reject(
    new AxiosError(`status ${status}`, 'ERR_BAD_REQUEST', config, null, {
      data: { detail: 'nope' },
      status,
      statusText: 'ERR',
      headers: new AxiosHeaders(),
      config,
    } as never)
  );
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('listUsers', () => {
  it('requests the moderated-users route with default paging', async () => {
    get.mockReturnValue(ok([{ id: 'u1' }]));

    const users = await admin.listUsers();

    expect(users).toEqual([{ id: 'u1' }]);
    expect(get).toHaveBeenCalledWith('/admin/api/v1/admin/users/moderated', {
      params: { limit: 50, offset: 0, status: undefined },
    });
  });

  it('forwards an explicit status filter', async () => {
    get.mockReturnValue(ok([]));

    await admin.listUsers({ status: 'suspended', limit: 10, offset: 20 });

    expect(get.mock.calls[0][1]).toStrictEqual({ params: { limit: 10, offset: 20, status: 'suspended' } });
  });

  it('sends no status filter rather than an empty string', async () => {
    get.mockReturnValue(ok([]));

    // An empty-string `status=` would be forwarded to FastAPI as a filter that
    // matches nothing, blanking the moderation table.
    await admin.listUsers({ status: '' });

    const params = (get.mock.calls[0][1] as { params: Params }).params as Record<string, unknown>;
    expect('status' in params && params.status === undefined).toBe(true);
  });

  it('does not send `search` to the backend (it is a client-side filter)', async () => {
    get.mockReturnValue(ok([]));

    await admin.listUsers({ search: 'ada' });

    const params = (get.mock.calls[0][1] as { params: Params }).params as Record<string, unknown>;
    expect(params).not.toHaveProperty('search');
  });

  it('propagates a rejection so the query layer can surface the error', async () => {
    get.mockReturnValue(boom(503));
    await expect(admin.listUsers()).rejects.toMatchObject({ response: { status: 503 } });
  });
});

describe('moderateUser', () => {
  it('posts user_id, status and reason in the body', async () => {
    post.mockReturnValue(ok({ status: 'banned' }));

    const out = await admin.moderateUser('u1', 'banned', 'chargeback fraud');

    expect(out).toEqual({ status: 'banned' });
    expect(post).toHaveBeenCalledWith('/admin/api/v1/admin/users/moderate', {
      user_id: 'u1',
      status: 'banned',
      reason: 'chargeback fraud',
    });
  });

  it('allows an omitted reason', async () => {
    post.mockReturnValue(ok({}));
    await admin.moderateUser('u1', 'active');
    expect(post.mock.calls[0][1]).toStrictEqual({ user_id: 'u1', status: 'active', reason: undefined });
  });

  it('propagates a rejection', async () => {
    post.mockReturnValue(boom(403));
    await expect(admin.moderateUser('u1', 'banned')).rejects.toMatchObject({ response: { status: 403 } });
  });
});

describe('flags', () => {
  it('defaults the flagged-content page size to 50', async () => {
    get.mockReturnValue(ok([]));

    await admin.listFlags();

    expect(get).toHaveBeenCalledWith('/admin/api/v1/admin/content/flagged', { params: { limit: 50, offset: 0 } });
  });

  it('sends content_id and status as query params with a null body', async () => {
    post.mockReturnValue(ok({ status: 'removed' }));

    await admin.resolveFlag('c1', 'removed');

    expect(post).toHaveBeenCalledWith('/admin/api/v1/admin/content/resolve', null, {
      params: { content_id: 'c1', status: 'removed' },
    });
  });
});

describe('alerts', () => {
  it('defaults the alert page size to 50', async () => {
    get.mockReturnValue(ok([]));

    await admin.listAlerts();

    expect(get).toHaveBeenCalledWith('/admin/api/v1/admin/alerts', { params: { limit: 50 } });
  });

  it('creates an alert with the full envelope', async () => {
    post.mockReturnValue(ok({ id: 9 }));

    const created = await admin.createAlert({
      alert_type: 'disk_pressure',
      severity: 'critical',
      message: 'content-service disk at 91%',
      service: 'content-service',
    });

    expect(created).toEqual({ id: 9 });
    expect(post).toHaveBeenCalledWith('/admin/api/v1/admin/alerts', {
      alert_type: 'disk_pressure',
      severity: 'critical',
      message: 'content-service disk at 91%',
      service: 'content-service',
    });
  });

  it('acknowledges by id on the acknowledge sub-route', async () => {
    post.mockReturnValue(ok({ acknowledged: true }));

    await admin.acknowledgeAlert(42);

    expect(post).toHaveBeenCalledWith('/admin/api/v1/admin/alerts/42/acknowledge');
  });

  it('propagates a rejection when acknowledging', async () => {
    post.mockReturnValue(boom(409));
    await expect(admin.acknowledgeAlert(42)).rejects.toMatchObject({ response: { status: 409 } });
  });
});

describe('system config', () => {
  it('defaults the config page size to 100', async () => {
    get.mockReturnValue(ok([]));

    await admin.listConfigs();

    expect(get).toHaveBeenCalledWith('/admin/api/v1/admin/config', { params: { limit: 100 } });
  });

  it('fetches a single config by key', async () => {
    get.mockReturnValue(ok({ key: 'SIGNUP_ENABLED', value: 'true' }));

    const config = await admin.getConfig('SIGNUP_ENABLED');

    expect(config).toEqual({ key: 'SIGNUP_ENABLED', value: 'true' });
    expect(get).toHaveBeenCalledWith('/admin/api/v1/admin/config/SIGNUP_ENABLED');
  });

  it('posts a config write with its type', async () => {
    post.mockReturnValue(ok({ key: 'MAX_CONCURRENT_STREAMS' }));

    await admin.setConfig({
      key: 'MAX_CONCURRENT_STREAMS',
      value: '4',
      config_type: 'integer',
      description: 'Per-user stream cap',
    });

    expect(post).toHaveBeenCalledWith('/admin/api/v1/admin/config', {
      key: 'MAX_CONCURRENT_STREAMS',
      value: '4',
      config_type: 'integer',
      description: 'Per-user stream cap',
    });
  });
});

describe('listAuditLogs routing', () => {
  it('queries by admin when admin_id is given', async () => {
    get.mockReturnValue(ok([{ id: 1 }]));

    const logs = await admin.listAuditLogs({ admin_id: 'a1', limit: 10 });

    expect(logs).toEqual([{ id: 1 }]);
    expect(get).toHaveBeenCalledWith('/admin/api/v1/admin/audit/admin/a1', { params: { limit: 10 } });
  });

  it('queries by resource when both type and id are given', async () => {
    get.mockReturnValue(ok([{ id: 2 }]));

    await admin.listAuditLogs({ resource_type: 'content', resource_id: 'c1' });

    expect(get).toHaveBeenCalledWith('/admin/api/v1/admin/audit/resource/content/c1', { params: { limit: 50 } });
  });

  it('prefers the admin filter when both are given', async () => {
    get.mockReturnValue(ok([]));

    await admin.listAuditLogs({ admin_id: 'a1', resource_type: 'content', resource_id: 'c1' });

    expect(get.mock.calls[0][0]).toBe('/admin/api/v1/admin/audit/admin/a1');
  });

  it('returns an empty list and issues no request when no filter is usable', async () => {
    expect(await admin.listAuditLogs()).toEqual([]);
    expect(get).not.toHaveBeenCalled();
  });

  it('returns an empty list when only one half of the resource filter is given', async () => {
    // resource_type without resource_id cannot address a resource; hitting the
    // list endpoint here would return every audit row on screen.
    expect(await admin.listAuditLogs({ resource_type: 'content' })).toEqual([]);
    expect(get).not.toHaveBeenCalled();
  });
});

describe('getSystemStats', () => {
  it('reads the stats route', async () => {
    get.mockReturnValue(ok({ total_users: 10 }));

    expect(await admin.getSystemStats()).toEqual({ total_users: 10 });
    expect(get).toHaveBeenCalledWith('/admin/api/v1/admin/stats');
  });

  it('propagates a rejection', async () => {
    get.mockReturnValue(boom(502));
    await expect(admin.getSystemStats()).rejects.toMatchObject({ response: { status: 502 } });
  });
});

describe('unwrapping contract', () => {
  it('returns the response body, not the axios envelope', async () => {
    get.mockReturnValue(ok({ id: 'x' }));
    // If a helper stopped destructuring `.data`, callers would render
    // `[object Object]` in the admin tables.
    const result = await admin.getConfig('k');
    expect(result).toEqual({ id: 'x' });
    expect((result as { data?: unknown }).data).toBeUndefined();
  });

  it('resolves to an array, not undefined, for a successful empty list', async () => {
    get.mockReturnValue(ok([]));
    const result = await admin.listUsers();
    expect(Array.isArray(result)).toBe(true);
    expect(result).toHaveLength(0);
  });
});
