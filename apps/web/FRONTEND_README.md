# Wildframe Web Frontend

React + Next.js 15 frontend for the Wildframe Netflix-like streaming platform.

## Features

- ✅ User authentication (login/signup)
- ✅ Content browsing with search
- ✅ Video streaming player
- ✅ Watch history tracking
- ✅ Responsive design
- ✅ Real-time notifications
- ✅ Account management
- ✅ Subscription management

## Tech Stack

- **Framework**: Next.js 15 (App Router)
- **UI Library**: React 19 RC
- **Styling**: Tailwind CSS
- **State Management**: Zustand
- **Data Fetching**: TanStack React Query
- **HTTP Client**: Axios
- **Video Player**: HLS.js + DASH.js
- **Testing**: Vitest + Playwright

## Setup

### 1. Install Dependencies
```bash
npm install
```

### 2. Environment Variables
Copy `.env.local.example` to `.env.local`:
```bash
cp .env.local.example .env.local
```

### 3. Run Development Server
```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Project Structure

```
src/
├── app/                  # Next.js pages (App Router)
│   ├── layout.tsx       # Root layout
│   ├── page.tsx         # Home page
│   ├── login/           # Login page
│   ├── signup/          # Signup page
│   ├── browse/          # Content browser
│   ├── watch/[id]/      # Video player page
│   ├── my-list/         # Watch history
│   ├── account/         # Account settings
│   └── billing/         # Billing page
├── components/          # React components
│   ├── layout/          # Layout components (Header, Footer)
│   ├── auth/            # Auth forms (Login, Signup)
│   ├── browse/          # Browse components (ContentCard, Grid)
│   ├── player/          # Video player components
│   └── common/          # Shared components (Skeleton, Button)
├── api/                 # API client
│   └── client.ts        # Axios client with interceptors
├── stores/              # Zustand stores
│   └── auth.ts          # Auth state management
├── hooks/               # Custom React hooks
│   └── index.ts         # useAuth, useUser hooks
├── types/               # TypeScript types
│   └── index.ts         # All type definitions
├── utils/               # Utility functions
│   └── index.ts         # Helpers (formatDate, truncate)
└── styles/              # CSS files
    └── globals.css      # Global styles
```

## Available Scripts

```bash
# Development
npm run dev              # Start dev server
npm run build            # Build for production
npm run start            # Start production server

# Quality
npm run lint             # Run ESLint
npm run lint:fix         # Fix linting issues
npm run format           # Format code with Prettier
npm run type-check       # Run TypeScript check

# Testing
npm run test             # Run unit tests
npm run test:ui          # Run tests with UI
npm run test:coverage    # Generate coverage report
npm run test:e2e         # Run E2E tests
npm run test:e2e:ui      # Run E2E tests with UI
npm run test:e2e:debug   # Debug E2E tests
```

## API Integration

The frontend integrates with the Wildframe backend via the API client:

```typescript
import { apiClient } from '@/api/client';

// Authentication
await apiClient.login(email, password);
await apiClient.register(email, password, firstName, lastName);

// Content
const movies = await apiClient.getMovies(20, 0);
const results = await apiClient.searchContent('action', 'movie');

// Streaming
const session = await apiClient.startStreaming(contentId, deviceId);
const manifest = await apiClient.getManifest(contentId, '1080p');

// Analytics
await apiClient.logEvent(userId, 'play_started', { quality: '1080p' });
```

## Authentication

Authentication is handled via JWT tokens:
- **Access Token**: Short-lived (15 min), stored in localStorage
- **Refresh Token**: Long-lived (7 days), automatically refreshed
- **Token Interceptor**: Automatically adds token to API requests
- **Protected Routes**: Middleware redirects unauthenticated users to login

## Video Player

Features:
- HLS/DASH adaptive streaming
- Quality selection (1080p, 720p, 480p, auto)
- Watch position tracking (auto-saved)
- Keyboard controls (play, pause, fullscreen)
- Playback speed adjustment

## State Management

Auth state is managed with Zustand:

```typescript
import { useAuthStore } from '@/stores/auth';

const auth = useAuthStore();
const { user, token, isAuthenticated } = auth;
const { login, logout, register } = auth;
```

## Testing

### Unit Tests
```bash
npm run test
```

### E2E Tests
```bash
npm run test:e2e
```

### Coverage
```bash
npm run test:coverage
```

## Performance Optimizations

- Code splitting with dynamic imports
- Image optimization with Next.js Image
- Lazy loading for components
- Caching with React Query
- Minimal bundle size

## Deployment

### Vercel (Recommended)
```bash
vercel deploy
```

### Docker
```bash
docker build -t wildframe-web .
docker run -p 3000:3000 wildframe-web
```

### Manual
```bash
npm run build
npm run start
```

## Troubleshooting

### API Connection Error
- Ensure backend is running on http://localhost:8000
- Check `.env.local` has correct `NEXT_PUBLIC_API_URL`
- Verify CORS is enabled in backend

### Authentication Issues
- Clear localStorage: `localStorage.clear()`
- Check token in DevTools Application tab
- Verify JWT token is valid

### Video Player Not Working
- Ensure HLS.js is installed: `npm install hls.js`
- Check manifest URL in browser console
- Verify CORS for video URLs

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/awesome-feature`
3. Make your changes
4. Commit: `git commit -am 'Add awesome feature'`
5. Push: `git push origin feature/awesome-feature`
6. Create a Pull Request

See [CONTRIBUTING.md](../../docs/CONTRIBUTING.md) for detailed guidelines.

## License

Internal project - Wildframe Netflix-like Platform

## Support

- Documentation: See [docs/](../../docs/)
- Issues: GitHub Issues
- API Reference: [API_DOCUMENTATION.md](../../docs/API_DOCUMENTATION.md)
