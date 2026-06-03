# 🎨 YourApp Frontend Template - Netlify Style

A modern, production-ready Next.js 15 template with clean design inspired by Netlify.

## 🚀 Quick Start

### 1. Install Dependencies
```bash
npm install
```

### 2. Create Environment File
```bash
cp .env.local.example .env.local
```

### 3. Start Development Server
```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

---

## 📁 Project Structure

```
src/
├── app/                    # Pages (App Router)
│   ├── page.tsx           # Landing page
│   ├── login/page.tsx     # Login page
│   ├── signup/page.tsx    # Signup page
│   ├── layout.tsx         # Root layout
│   └── globals.css        # Global styles
│
├── components/            # Reusable components
│   └── common/            # Shared components
│       ├── Button.tsx     # Customizable button
│       ├── Card.tsx       # Card container
│       ├── Badge.tsx      # Status badge
│       ├── Section.tsx    # Section wrapper
│       ├── Container.tsx  # Content container
│       ├── Grid.tsx       # Grid layout
│       └── index.ts       # Exports
│
└── app.tsx               # Main app component
```

---

## 🎯 Available Pages

### Public Pages
- **`/`** - Landing page with hero, features, pricing, testimonials
- **`/login`** - User login form
- **`/signup`** - Account registration form

### Add More Pages
```bash
# Create a new page
mkdir -p src/app/dashboard
touch src/app/dashboard/page.tsx
```

---

## 🧩 Reusable Components

### Button
```tsx
import { Button } from '@/components/common';

export default function Example() {
  return (
    <>
      <Button variant="primary" size="md">Primary</Button>
      <Button variant="secondary" size="lg">Secondary</Button>
      <Button variant="ghost">Ghost</Button>
    </>
  );
}
```

**Variants:** `primary` (filled), `secondary` (outlined), `ghost` (text)  
**Sizes:** `sm`, `md`, `lg`

### Card
```tsx
import { Card } from '@/components/common';

export default function Example() {
  return (
    <Card hover className="p-6">
      <h3>Card Title</h3>
      <p>Card content goes here</p>
    </Card>
  );
}
```

### Section
```tsx
import { Section, Container, Grid } from '@/components/common';

export default function Example() {
  return (
    <Section title="Our Features" subtitle="Everything you need" dark>
      <Container>
        <Grid cols={3}>
          {/* Grid content */}
        </Grid>
      </Container>
    </Section>
  );
}
```

### Grid
```tsx
import { Grid } from '@/components/common';

export default function Example() {
  return (
    <Grid cols={3} gap="md">
      <div>Item 1</div>
      <div>Item 2</div>
      <div>Item 3</div>
    </Grid>
  );
}
```

**Cols:** `1`, `2`, `3`, `4`  
**Gap:** `sm` (gap-4), `md` (gap-8), `lg` (gap-12)

### Badge
```tsx
import { Badge } from '@/components/common';

export default function Example() {
  return (
    <>
      <Badge variant="default">Default</Badge>
      <Badge variant="success">Success</Badge>
      <Badge variant="warning">Warning</Badge>
      <Badge variant="error">Error</Badge>
    </>
  );
}
```

---

## 🎨 Styling

The template uses **Tailwind CSS** for styling.

### Customizing Colors
Edit `tailwind.config.ts`:
```js
module.exports = {
  theme: {
    extend: {
      colors: {
        primary: '#1f2937', // Change brand color
      },
    },
  },
};
```

### Updating the Palette
Current colors:
- **Primary:** Gray-900 (dark gray)
- **Background:** White
- **Borders:** Gray-200
- **Text:** Gray-900 (dark)
- **Secondary:** Gray-600

---

## 🔧 Common Customizations

### Change App Name
1. Update `NEXT_PUBLIC_APP_NAME` in `.env.local`
2. Update `metadata.title` in `src/app/layout.tsx`
3. Change logo text in landing page

### Update Pricing
Edit `/src/app/page.tsx` and find the pricing section:
```tsx
const plans = [
  {
    name: 'Starter',
    price: 'Free',
    features: ['Feature 1', 'Feature 2'],
  },
  // Add more plans
];
```

### Add Navigation Links
Edit the navigation in `/src/app/page.tsx`:
```tsx
<a href="#features">Features</a>
<a href="#pricing">Pricing</a>
<a href="#custom">Your Link</a>
```

### Customize Colors/Branding
- **Font:** Change in `src/app/globals.css`
- **Colors:** Update Tailwind config or inline classes
- **Logo:** Replace text with image

---

## 📝 Scripts

```bash
# Development
npm run dev              # Start dev server

# Production
npm run build            # Build for production
npm run start            # Start production server

# Code Quality
npm run lint             # Run ESLint
npm run lint:fix         # Fix linting issues
npm run format           # Format with Prettier
npm run type-check       # Check TypeScript

# Testing (optional)
npm run test             # Run tests
npm run test:e2e         # Run E2E tests
```

---

## 🔗 Connecting to Backend

### Enable API Integration
1. Uncomment in `.env.local`:
   ```
   NEXT_PUBLIC_API_URL=http://localhost:8000
   ```

2. Create API client:
   ```tsx
   // src/api/client.ts
   import axios from 'axios';

   const client = axios.create({
     baseURL: process.env.NEXT_PUBLIC_API_URL,
   });

   export default client;
   ```

3. Use in components:
   ```tsx
   import client from '@/api/client';

   const response = await client.get('/api/users');
   ```

---

## 🔐 Authentication

### Add Login/Signup Logic
Edit `/src/app/login/page.tsx` and `/src/app/signup/page.tsx`:

```tsx
const handleLogin = async (e: React.FormEvent) => {
  e.preventDefault();
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await response.json();
    // Handle response
  } catch (error) {
    console.error(error);
  }
};
```

---

## 📱 Responsive Design

The template is mobile-first and uses Tailwind's responsive prefixes:

```tsx
<div className="text-center md:text-left lg:grid-cols-3">
  // Mobile: centered, Tablet: left-aligned, Desktop: 3 columns
</div>
```

Common breakpoints:
- `sm`: 640px
- `md`: 768px
- `lg`: 1024px
- `xl`: 1280px
- `2xl`: 1536px

---

## 🚀 Deployment

### Vercel (Recommended)
```bash
vercel deploy
```

### Netlify
```bash
npm run build
netlify deploy --prod --dir=.next
```

### Docker
```bash
docker build -t yourapp-web .
docker run -p 3000:3000 yourapp-web
```

### Environment Variables
Set these in your deployment platform:
```
NEXT_PUBLIC_APP_NAME=YourApp
NEXT_PUBLIC_APP_URL=https://yourapp.com
NEXT_PUBLIC_API_URL=https://api.yourapp.com
```

---

## 📚 Dependencies

```json
{
  "next": "^15.0.0",
  "react": "^19.0.0-rc",
  "react-dom": "^19.0.0-rc",
  "tailwindcss": "^4.0.0",
  "typescript": "^5.2.0"
}
```

Optional:
- `axios` - HTTP requests
- `zustand` - State management
- `@tanstack/react-query` - Server state
- `clsx` - Conditional classes
- `date-fns` - Date formatting

---

## 🐛 Troubleshooting

### Issue: Styles not loading
**Solution:** Restart dev server and clear `.next` folder
```bash
rm -rf .next
npm run dev
```

### Issue: Port 3000 already in use
**Solution:** Kill process or use different port
```bash
npm run dev -- -p 3001
```

### Issue: TypeScript errors
**Solution:** Generate types
```bash
npm run type-check
```

---

## 📖 Further Customization

### Add a Blog
```bash
mkdir -p src/app/blog/[slug]
touch src/app/blog/page.tsx
touch src/app/blog/[slug]/page.tsx
```

### Add Admin Dashboard
```bash
mkdir -p src/app/admin
touch src/app/admin/page.tsx
```

### Add API Routes (Optional)
```bash
mkdir -p src/app/api/hello
touch src/app/api/hello/route.ts
```

---

## 🎓 Learning Resources

- [Next.js Documentation](https://nextjs.org/docs)
- [Tailwind CSS Docs](https://tailwindcss.com/docs)
- [React Documentation](https://react.dev)
- [TypeScript Handbook](https://www.typescriptlang.org/docs)

---

## 📄 License

Open for personal and commercial use. Modify freely.

---

## 💡 Next Steps

1. ✅ Customize branding (colors, logo, copy)
2. ✅ Add your own content sections
3. ✅ Connect backend API
4. ✅ Add authentication
5. ✅ Deploy to production

**Happy building!** 🚀
