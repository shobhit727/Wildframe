# 🔗 API Integration Guide

Learn how to connect your frontend to a backend API.

## Setup

### 1. Enable API URL
Edit `.env.local`:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 2. Create API Client
Create `src/api/client.ts`:
```typescript
import axios from 'axios';

const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add request interceptor for auth token
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('authToken');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Add response interceptor for error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Handle unauthorized
      localStorage.removeItem('authToken');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

export default apiClient;
```

### 3. Install axios
```bash
npm install axios
```

---

## Basic Requests

### GET Request
```typescript
import apiClient from '@/api/client';

// Fetch data
const response = await apiClient.get('/api/users');
console.log(response.data);

// With parameters
const response = await apiClient.get('/api/users', {
  params: { page: 1, limit: 10 }
});
```

### POST Request
```typescript
// Send data
const response = await apiClient.post('/api/users', {
  name: 'John Doe',
  email: 'john@example.com',
});
console.log(response.data);
```

### PUT Request
```typescript
// Update data
const response = await apiClient.put('/api/users/123', {
  name: 'Jane Doe',
});
```

### DELETE Request
```typescript
// Delete data
const response = await apiClient.delete('/api/users/123');
```

---

## Using in Components

### Fetch Data on Mount
```tsx
'use client';

import { useEffect, useState } from 'react';
import apiClient from '@/api/client';

export default function UsersList() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchUsers = async () => {
      try {
        const response = await apiClient.get('/api/users');
        setUsers(response.data);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchUsers();
  }, []);

  if (loading) return <p>Loading...</p>;
  if (error) return <p>Error: {error}</p>;

  return (
    <ul>
      {users.map((user) => (
        <li key={user.id}>{user.name}</li>
      ))}
    </ul>
  );
}
```

### Submit Form
```tsx
'use client';

import { useState } from 'react';
import { Button } from '@/components/common';
import apiClient from '@/api/client';

export default function LoginForm() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const response = await apiClient.post('/api/auth/login', {
        email,
        password,
      });

      // Save token
      localStorage.setItem('authToken', response.data.token);

      // Redirect
      window.location.href = '/dashboard';
    } catch (err: any) {
      setError(err.response?.data?.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Email"
        required
      />
      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Password"
        required
      />
      {error && <p className="text-red-600">{error}</p>}
      <Button type="submit" disabled={loading}>
        {loading ? 'Logging in...' : 'Log in'}
      </Button>
    </form>
  );
}
```

---

## Error Handling

### Try-Catch Pattern
```typescript
try {
  const response = await apiClient.get('/api/data');
  console.log(response.data);
} catch (error: any) {
  const message = error.response?.data?.message || error.message;
  console.error('Error:', message);
}
```

### Status Code Handling
```typescript
try {
  const response = await apiClient.get('/api/data');
  console.log(response.data);
} catch (error: any) {
  if (error.response) {
    // Server responded with error
    const { status, data } = error.response;
    
    if (status === 400) {
      console.error('Bad request:', data.message);
    } else if (status === 401) {
      console.error('Unauthorized');
    } else if (status === 404) {
      console.error('Not found');
    } else if (status === 500) {
      console.error('Server error');
    }
  } else {
    // Network error
    console.error('Network error:', error.message);
  }
}
```

---

## Authentication

### Login
```typescript
const login = async (email: string, password: string) => {
  const response = await apiClient.post('/api/auth/login', {
    email,
    password,
  });

  // Save tokens
  localStorage.setItem('accessToken', response.data.accessToken);
  localStorage.setItem('refreshToken', response.data.refreshToken);

  return response.data;
};
```

### Logout
```typescript
const logout = () => {
  localStorage.removeItem('accessToken');
  localStorage.removeItem('refreshToken');
  window.location.href = '/login';
};
```

### Auto Refresh Token
```typescript
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;

      try {
        const refreshToken = localStorage.getItem('refreshToken');
        const response = await apiClient.post('/api/auth/refresh', {
          refreshToken,
        });

        localStorage.setItem('accessToken', response.data.accessToken);
        originalRequest.headers.Authorization = `Bearer ${response.data.accessToken}`;

        return apiClient(originalRequest);
      } catch (refreshError) {
        // Refresh failed, redirect to login
        window.location.href = '/login';
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);
```

---

## Using React Query (Optional)

Install React Query:
```bash
npm install @tanstack/react-query axios
```

Setup:
```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const queryClient = new QueryClient();

export default function App({ children }) {
  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}
```

Fetch data:
```tsx
import { useQuery } from '@tanstack/react-query';
import apiClient from '@/api/client';

export default function UsersList() {
  const { data: users, isLoading, error } = useQuery({
    queryKey: ['users'],
    queryFn: async () => {
      const response = await apiClient.get('/api/users');
      return response.data;
    },
  });

  if (isLoading) return <p>Loading...</p>;
  if (error) return <p>Error: {error.message}</p>;

  return (
    <ul>
      {users?.map((user) => (
        <li key={user.id}>{user.name}</li>
      ))}
    </ul>
  );
}
```

---

## Common Endpoints

### Authentication
```
POST   /api/auth/register      - Create account
POST   /api/auth/login         - Login
POST   /api/auth/logout        - Logout
POST   /api/auth/refresh       - Refresh token
```

### Users
```
GET    /api/users/{id}         - Get user
PUT    /api/users/{id}         - Update user
DELETE /api/users/{id}         - Delete user
GET    /api/users              - List users
```

### Content
```
GET    /api/content            - List content
GET    /api/content/{id}       - Get content
POST   /api/content            - Create content
PUT    /api/content/{id}       - Update content
DELETE /api/content/{id}       - Delete content
```

---

## Tips

1. **Always use environment variables** for API URLs
2. **Handle errors gracefully** - show user-friendly messages
3. **Show loading states** while fetching
4. **Use TypeScript** for type safety
5. **Store tokens securely** (localStorage for SPAs)
6. **Implement auto-refresh** for token expiration
7. **CORS** - Configure backend for frontend origin
