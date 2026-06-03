"""Frontend TypeScript types."""
export interface User {
  id: string;
  email: string;
  firstName: string;
  lastName: string;
  avatar?: string;
}

export interface AuthResponse {
  accessToken: string;
  refreshToken: string;
  user: User;
}

export interface Content {
  id: string;
  title: string;
  description: string;
  genre: string;
  poster: string;
  backdrop: string;
  duration: number;
  releaseDate: string;
  rating: number;
  type: 'movie' | 'show';
}

export interface StreamingSession {
  id: string;
  contentId: string;
  deviceId: string;
  status: 'active' | 'paused' | 'ended';
  currentPosition: number;
  startTime: string;
}

export interface Subscription {
  id: string;
  tier: 'free' | 'basic' | 'premium' | 'family';
  price: number;
  features: string[];
  status: 'active' | 'cancelled';
}

export interface Notification {
  id: string;
  title: string;
  message: string;
  read: boolean;
  timestamp: string;
}
