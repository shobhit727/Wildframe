// API client for backend services.
import axios, { AxiosInstance } from 'axios';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

class APIClient {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: API_BASE_URL,
      timeout: 10000,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Add token to requests
    this.client.interceptors.request.use((config) => {
      const token = typeof window !== 'undefined' ? localStorage.getItem('accessToken') : null;
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
      return config;
    });

    // Handle responses
    this.client.interceptors.response.use(
      (response) => response,
      (error) => {
        if (error.response?.status === 401) {
          if (typeof window !== 'undefined') {
            localStorage.removeItem('accessToken');
            window.location.href = '/login';
          }
        }
        return Promise.reject(error);
      }
    );
  }

  // Auth
  register(email: string, password: string, firstName: string, lastName: string) {
    return this.client.post('/auth/register', {
      email,
      password,
      first_name: firstName,
      last_name: lastName,
    });
  }

  login(email: string, password: string) {
    return this.client.post('/auth/login', { email, password });
  }

  logout() {
    return this.client.post('/auth/logout');
  }

  verifyEmail(token: string) {
    return this.client.get(`/auth/verify-email/${token}`);
  }

  // User
  getProfile() {
    return this.client.get('/users/me');
  }

  updateProfile(data: Record<string, unknown>) {
    return this.client.put('/users/me', data);
  }

  // Content
  getMovies(limit = 20, offset = 0) {
    return this.client.get('/content/movies', { params: { limit, offset } });
  }

  getShows(limit = 20, offset = 0) {
    return this.client.get('/content/shows', { params: { limit, offset } });
  }

  searchContent(query: string, type?: string) {
    return this.client.get('/content/search', { params: { q: query, type } });
  }

  getContentById(id: string) {
    return this.client.get(`/content/movies/${id}`);
  }

  getGenres() {
    return this.client.get('/content/genres');
  }

  // Streaming
  startStreaming(contentId: string, deviceId: string) {
    return this.client.post('/streaming/session/start', { content_id: contentId, device_id: deviceId });
  }

  getManifest(contentId: string, quality = 'auto') {
    return this.client.get(`/streaming/manifest/${contentId}`, { params: { quality } });
  }

  updateWatchPosition(sessionId: string, positionSeconds: number) {
    return this.client.put(`/streaming/session/${sessionId}/position`, { position_seconds: positionSeconds });
  }

  endSession(sessionId: string) {
    return this.client.post(`/streaming/session/${sessionId}/end`);
  }

  getWatchHistory(userId: string) {
    return this.client.get(`/streaming/watch-history/${userId}`);
  }

  // Search
  search(query: string, type?: string, limit = 20) {
    return this.client.get('/search/query', { params: { q: query, content_type: type, limit } });
  }

  getTrending(type?: string) {
    return this.client.get('/search/trending', { params: { content_type: type } });
  }

  // Recommendations
  getRecommendations(userId: string, limit = 20) {
    return this.client.get(`/recommendations/for-user/${userId}`, { params: { limit } });
  }

  // Billing
  getSubscription(userId: string) {
    return this.client.get(`/billing/subscription/${userId}`);
  }

  upgradeSubscription(userId: string, tier: string) {
    return this.client.post(`/billing/upgrade/${userId}`, { tier });
  }

  // Analytics
  logEvent(userId: string, eventType: string, eventData?: Record<string, unknown>, contentId?: string) {
    return this.client.post('/analytics/events', {
      user_id: userId,
      event_type: eventType,
      event_data: eventData,
      content_id: contentId,
    });
  }
}

export const apiClient = new APIClient();
