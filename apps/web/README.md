# Wildframe Frontend

Production-grade Next.js application for the Wildframe streaming platform.

## Quick Start

### Prerequisites

- Node.js 18+ (20+ recommended)
- npm or yarn

### Local Development

```bash
# Install dependencies
npm install

# Set up environment variables
cp .env.example .env.local

# Run development server
npm run dev

# Open https://localhost:3000
```

### Available Scripts

```bash
# Development
npm run dev          # Start dev server with hot reload
npm run build        # Build for production
npm run start        # Start production server

# Code Quality
npm run lint         # Run ESLint
npm run lint:fix     # Fix linting issues
npm run format       # Format code with Prettier
npm run type-check   # Type check TypeScript

# Testing
npm run test         # Run unit tests
npm run test:ui      # Run tests with UI
npm run test:coverage # Coverage report
npm run test:e2e     # Run E2E tests
npm run test:e2e:ui  # Run E2E tests with UI
```

## Project Structure

```
src/
├── app/                    # Next.js app router pages
│   ├── layout.tsx         # Root layout
│   ├── page.tsx           # Home page
│   ├── auth/              # Auth pages (login, register)
│   ├── browse/            # Content browsing
│   ├── watch/[id]         # Video player
│   ├── profile/           # User profile
│   └── admin/             # Admin dashboard
├── components/            # React components
│   ├── common/            # Reusable UI components
│   ├── layout/            # Layout components
│   ├── player/            # Video player
│   └── content/           # Content components
├── hooks/                 # Custom React hooks
├── lib/                   # Utility functions
│   └── api/              # API client
├── services/              # Business logic services
├── stores/                # Zustand state management
├── types/                 # TypeScript types
├── config/                # Configuration
├── constants/             # Constants
└── styles/                # Global styles
```

## Environment Variables

### Development

Create `.env.local`:

```env
NEXT_PUBLIC_API_URL=https://localhost:8000
NEXT_PUBLIC_IMAGE_URL=https://localhost:8000/images
NEXT_PUBLIC_GA_ID=your-ga-id
NEXT_PUBLIC_STRIPE_KEY=pk_test_your-key
```

### Production

```env
NEXT_PUBLIC_API_URL=https://api.wildframe.com
NEXT_PUBLIC_IMAGE_URL=https://images.wildframe.com
NEXT_PUBLIC_GA_ID=your-ga-id
NEXT_PUBLIC_STRIPE_KEY=pk_live_your-key
```

## Features

### Authentication

- Email/password login and registration
- JWT token management
- Automatic token refresh
- Secure httpOnly cookies
- MFA support (future)

### Content Discovery

- Browse by genre
- Search functionality
- Trending content
- Personalized recommendations
- Infinite scroll pagination

### Video Playback

- HLS/DASH support
- Adaptive bitrate streaming
- Quality selector
- Subtitle support
- Keyboard shortcuts
- Fullscreen and theater mode
- Resume playback

### User Management

- Profile management
- Device tracking
- Viewing history
- Watchlist management
- Subscription management
- Account preferences

## Performance

### Optimizations

- Image optimization with Next.js Image
- Code splitting and lazy loading
- CSS optimization with Tailwind
- API response caching
- Browser caching strategy

### Targets

- Lighthouse Score: 90+
- First Contentful Paint: < 1.5s
- Time to Interactive: < 3s
- Core Web Vitals: All green

### Current Metrics

Monitor with:
```bash
npm run build
npm run start
```

Then use Chrome DevTools → Lighthouse or WebPageTest.

## Testing

### Unit Tests

```bash
npm run test
```

Write tests in `*.test.ts(x)` files.

### Integration Tests

Create tests with API mocking:

```typescript
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

describe("Login", () => {
  it("should login user", async () => {
    render(<LoginPage />);
    // ... test implementation
  });
});
```

### E2E Tests

```bash
npm run test:e2e
```

Write tests in `tests/e2e/` directory.

## Styling

### Tailwind CSS

Primary styling solution with custom theme:

```jsx
<div className="bg-primary-600 text-white px-4 py-2 rounded-lg">
  Button
</div>
```

### CSS Modules

For component-specific styles:

```jsx
import styles from "./Button.module.css";

export function Button() {
  return <button className={styles.button}>Click me</button>;
}
```

### Dark Mode

Automatic theme switching with `next-themes`:

```jsx
import { useTheme } from "next-themes";

export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme();
  // ...
}
```

## API Integration

### Axios Client

All API requests go through `lib/api/client.ts`:

```typescript
import { api } from "@/lib/api/client";

// GET request
const data = await api.get("/content");

// POST request with data
const response = await api.post("/auth/login", {
  email: "user@example.com",
  password: "password123",
});
```

### Error Handling

Automatic error handling with retry logic:

```typescript
try {
  const data = await api.get("/content");
} catch (error) {
  if (error.response?.status === 401) {
    // Handle unauthorized
  }
}
```

### Authentication

Token management automatic:

```typescript
// Token automatically added to requests
// Refresh token automatic on expiry
```

## State Management

### Zustand Stores

```typescript
import { useAuthStore } from "@/stores/authStore";

export function Component() {
  const { user, login, logout } = useAuthStore();
  // ...
}
```

### TanStack Query

```typescript
import { useQuery } from "@tanstack/react-query";

export function Component() {
  const { data, isLoading } = useQuery({
    queryKey: ["content"],
    queryFn: () => api.get("/content"),
  });
  // ...
}
```

## Custom Hooks

### useAuth

```typescript
import { useAuth } from "@/hooks/useAuth";

export function Component() {
  const { user, isLoading, login } = useAuth();
  // ...
}
```

### useContent

```typescript
import { useContent } from "@/hooks/useContent";

export function Component() {
  const { content, isLoading } = useContent({ genre: "action" });
  // ...
}
```

## Deployment

### Vercel (Recommended)

```bash
# Push to GitHub
git push origin main

# Vercel auto-deploys on push
# Set environment variables in Vercel dashboard
```

### Docker

```bash
# Build image
docker build -t wildframe-web .

# Run container
docker run -p 3000:3000 \
  -e NEXT_PUBLIC_API_URL=https://api.wildframe.com \
  wildframe-web
```

### AWS S3 + CloudFront

```bash
# Build
npm run build

# Upload to S3
aws s3 sync out/ s3://wildframe-web --delete

# Invalidate CloudFront
aws cloudfront create-invalidation \
  --distribution-id E123ABC \
  --paths "/*"
```

### Manual VPS

```bash
# Build
npm run build

# Transfer to VPS
scp -r .next/ user@vps:/app/

# Start on VPS
pm2 start "npm start" --name wildframe-web
```

## Troubleshooting

### Port Already in Use

```bash
lsof -i :3000
kill -9 <PID>
```

### Module Not Found

```bash
rm -rf node_modules .next
npm install
npm run dev
```

### TypeScript Errors

```bash
npm run type-check
```

### API Connection Failed

Check:
1. API server running on `NEXT_PUBLIC_API_URL`
2. CORS headers correct
3. Environment variables set

### Performance Issues

Check:
1. Lighthouse scores
2. Network tab in DevTools
3. Bundle size: `npm run build -- --analyze`
4. Web Vitals: `npm run test`

## Browser Support

- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+
- iOS Safari 14+
- Chrome Android 90+

## Documentation

- [Frontend Architecture](../../docs/FRONTEND_ARCHITECTURE.md)
- [Contributing Guide](../../docs/CONTRIBUTING.md)
- [Next.js Docs](https://nextjs.org/docs)
- [TypeScript Docs](https://www.typescriptlang.org/docs/)
- [Tailwind CSS](https://tailwindcss.com/docs)
- [TanStack Query](https://tanstack.com/query/latest)
- [Zustand](https://github.com/pmndrs/zustand)

## License

Proprietary Wildframe License. All rights reserved.
