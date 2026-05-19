# Wildframe Frontend Architecture

This document describes the Next.js frontend application for the Wildframe streaming platform.

## Technology Stack

- **Framework**: Next.js 15 (App Router)
- **Language**: TypeScript 5
- **Styling**: Tailwind CSS 4 + CSS Modules
- **State Management**: TanStack Query + Zustand
- **Video Player**: HLS.js + Dash.js
- **UI Components**: Headless UI + Radix UI
- **Testing**: Vitest + Playwright
- **Build**: Turbopack
- **Deployment**: Vercel / AWS CloudFront

## Project Structure

```
apps/web/
├── src/
│   ├── app/               # Next.js app router pages
│   │   ├── layout.tsx     # Root layout
│   │   ├── page.tsx       # Home page
│   │   ├── auth/          # Authentication pages
│   │   ├── browse/        # Content browsing
│   │   ├── watch/         # Video player
│   │   ├── profile/       # User profile
│   │   ├── admin/         # Admin dashboard
│   │   └── api/           # API routes (if needed)
│   ├── components/        # Reusable components
│   │   ├── common/        # Common UI components
│   │   ├── layout/        # Layout components
│   │   ├── player/        # Video player components
│   │   ├── content/       # Content display components
│   │   └── forms/         # Form components
│   ├── hooks/             # Custom React hooks
│   │   ├── useAuth.ts     # Authentication hook
│   │   ├── useContent.ts  # Content fetching
│   │   ├── usePlayback.ts # Playback management
│   │   └── useLocalStorage.ts
│   ├── lib/               # Utility functions
│   │   ├── api/           # API client
│   │   ├── auth/          # Auth utilities
│   │   ├── video/         # Video utilities
│   │   └── formatting.ts
│   ├── services/          # Business logic services
│   │   ├── authService.ts
│   │   ├── contentService.ts
│   │   ├── playbackService.ts
│   │   └── recommendationService.ts
│   ├── stores/            # Zustand stores
│   │   ├── authStore.ts
│   │   ├── playerStore.ts
│   │   └── uiStore.ts
│   ├── types/             # TypeScript type definitions
│   │   ├── models.ts      # Data models
│   │   ├── api.ts         # API types
│   │   └── index.ts
│   ├── styles/            # Global styles
│   ├── config/            # Configuration
│   └── constants/         # Constants
├── public/                # Static assets
├── tests/                 # Test files
├── package.json
├── tsconfig.json
├── tailwind.config.ts
└── next.config.ts
```

## Key Features

### Authentication Flow

```
1. User lands on app
2. Check localStorage for refresh token
3. If valid: refresh access token
4. If invalid: redirect to login
5. Store tokens in secure httpOnly cookie
6. Set up axios interceptor to refresh token on 401
```

### Video Player

```
HLS.js / Dash.js
├── Auto quality selection
├── Manual quality picker
├── Subtitle support
├── Keyboard shortcuts
├── Fullscreen support
├── Picture-in-picture
├── Resume playback
└── Progress tracking
```

### Content Discovery

```
Browse Page
├── Genres/Categories
├── Trending content
├── Personalized recommendations
├── Search functionality
├── Infinite scroll/pagination
└── Filters (rating, release date, etc.)
```

### User Features

```
Profile Management
├── Update profile info
├── Change password
├── Device management
├── Account preferences
├── Subscription status
└── Billing history
```

## Component Architecture

### Layout Components
```typescript
// Root layout
- Header (nav, user menu, search)
- Sidebar (genres, watchlist)
- Main content area
- Footer

// Page layouts
- ContentBrowseLayout (header + sidebar + grid)
- PlayerLayout (full screen or theater mode)
- ProfileLayout (sidebar nav + content)
```

### Content Components
```typescript
// ContentCard
- Thumbnail
- Title
- Rating/badges
- Hover preview
- Play button

// ContentGrid
- Responsive grid (1-6 columns)
- Loading skeleton
- Empty state
- Error state

// ContentDetail
- Hero image
- Title, description
- Rating, badges
- Cast/crew
- Episodes (for shows)
- Action buttons (play, add to list)
```

### Player Components
```typescript
// VideoPlayer
- HLS/DASH player
- Custom controls
- Quality selector
- Subtitle selector
- Settings menu

// PlaybackUI
- Play/pause button
- Progress bar
- Volume control
- Fullscreen button
- Theater mode button
```

## State Management

### Zustand Stores

```typescript
// authStore
- user: User | null
- accessToken: string
- refreshToken: string
- isLoading: boolean
- login(email, password)
- logout()
- refresh()

// playerStore
- currentVideo: Video
- isPlaying: boolean
- currentTime: number
- duration: number
- volume: number
- quality: 'auto' | '1080p' | '720p' | ...
- subtitles: boolean
- pause()
- play()
- seek(time)
- setQuality(quality)

// uiStore
- sidebarOpen: boolean
- theme: 'light' | 'dark'
- toggleSidebar()
- setTheme(theme)
```

### TanStack Query

```typescript
// Content queries
- useContentList({ genre?, page? })
- useContentDetail(id)
- useSearchContent(query)
- useTrendingContent()
- useRecommendations(userId)

// User queries
- useUser()
- useWatchlist()
- useContinueWatching()
- useUserDevices()

// Playback queries
- usePlaybackSession(videoId)
- useWatchProgress(videoId)
```

## API Integration

### Axios Client

```typescript
// Base setup
- Base URL from env
- Request interceptor (add auth token)
- Response interceptor (handle 401, refresh token)
- Error handling (format errors)

// Request types
- GET /api/content - List content
- GET /api/content/{id} - Get detail
- POST /api/playback/sessions - Create session
- POST /api/playback/sessions/{id}/events - Track progress
- PUT /api/users/me - Update profile
```

## Styling Strategy

### Tailwind CSS + Modules

```typescript
// Global styles
- Design tokens
- Color palette
- Typography scale
- Spacing scale
- Breakpoints

// Component styles
- CSS Modules for component-specific styles
- Tailwind for utility classes
- CSS variables for theme

// Responsive design
- Mobile-first approach
- Breakpoints: sm, md, lg, xl, 2xl
- Flexible grid/flex layouts
```

## Performance Optimizations

### Images
```typescript
- Next.js Image component
- Lazy loading
- WebP format
- Responsive srcSet
- Blur placeholder
```

### Code Splitting
```typescript
- Route-based splitting (automatic)
- Dynamic imports for heavy components
- Lazy load video player library
```

### Caching
```typescript
- Browser cache for images
- API response caching with TanStack Query
- localStorage for preferences
- IndexedDB for offline playback (future)
```

## Testing

### Unit Tests
```bash
npm run test:unit
```

```typescript
// Example test
describe('VideoPlayer', () => {
  it('should display play button initially', () => {
    render(<VideoPlayer />);
    expect(screen.getByRole('button', { name: /play/i })).toBeInTheDocument();
  });
});
```

### Integration Tests
```bash
npm run test:integration
```

```typescript
// Example test
describe('Content Browse', () => {
  it('should load and display content list', async () => {
    render(<BrowsePage />);
    await waitFor(() => {
      expect(screen.getAllByRole('article')).toHaveLength(20);
    });
  });
});
```

### E2E Tests
```bash
npm run test:e2e
```

```typescript
// Example test
test('User can login and watch video', async ({ page }) => {
  await page.goto('/login');
  await page.fill('[name="email"]', 'user@example.com');
  await page.fill('[name="password"]', 'password123');
  await page.click('button[type="submit"]');
  await page.waitForURL('/');
  await page.click('[data-content-id="123"]');
  await page.waitForURL('/watch/123');
  await expect(page.locator('video')).toBeVisible();
});
```

## Accessibility

### WCAG 2.1 Level AA Compliance

```typescript
// Keyboard navigation
- Tab through interactive elements
- Enter/Space to activate buttons
- Arrow keys in sliders/menus

// Screen readers
- Semantic HTML
- ARIA labels
- Alt text for images
- Form labels

// Visual
- Color contrast (4.5:1 for text)
- Focus indicators
- No color alone for meaning
```

## Security

### Authentication
```typescript
- Secure httpOnly cookies for tokens
- CSRF protection
- XSS prevention (React escapes by default)
- CSP headers
```

### API Communication
```typescript
- HTTPS only
- Authorization headers
- Validate server responses
- No sensitive data in localStorage
```

## Environment Configuration

### `.env.local`
```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_STRIPE_KEY=pk_test_...
NEXT_PUBLIC_GA_ID=UA-...
```

### `.env.production`
```
NEXT_PUBLIC_API_URL=https://api.wildframe.com
NEXT_PUBLIC_STRIPE_KEY=pk_live_...
NEXT_PUBLIC_GA_ID=UA-...
```

## Development Workflow

### Local Development
```bash
npm run dev
# Open http://localhost:3000
```

### Build for Production
```bash
npm run build
npm run start
```

### Linting & Formatting
```bash
npm run lint
npm run format
```

## Deployment

### Vercel
```bash
# Connect GitHub repository
# Set environment variables
# Automatic deployment on push
```

### AWS S3 + CloudFront
```bash
npm run build
aws s3 sync out/ s3://wildframe-web
aws cloudfront create-invalidation --distribution-id E123 --paths "/*"
```

## Browser Support

- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+
- iOS Safari 14+
- Chrome Android 90+

## Performance Targets

- Lighthouse Score: 90+
- First Contentful Paint: < 1.5s
- Time to Interactive: < 3s
- Cumulative Layout Shift: < 0.1
- Core Web Vitals: All green

## Future Enhancements

- [ ] Progressive Web App (PWA)
- [ ] Offline viewing with IndexedDB
- [ ] Advanced search filters
- [ ] User-generated reviews
- [ ] Social sharing
- [ ] Multi-profile support
- [ ] Download for offline viewing
- [ ] Live streaming support
- [ ] Interactive content
- [ ] DRM support (Widevine)

---

Last Updated: 2026-05-12
