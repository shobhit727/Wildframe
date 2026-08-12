# 🎨 YourApp - Next.js Netlify-Style Template

**A clean, modern, production-ready frontend template inspired by Netlify's design.**

Perfect for building SaaS products, landing pages, and web applications.

---

## ⚡ Quick Start (2 minutes)

```bash
cd /home/phoenix/Desktop/wildframe/apps/web

# Install dependencies
npm install

# Start development server
npm run dev

# Open https://localhost:3000
```

---

## 📦 What's Included

### 🎯 Pages
- **Landing Page** (`/`) - Hero section, features, pricing, testimonials
- **Login Page** (`/login`) - User authentication form
- **Signup Page** (`/signup`) - Account registration form

### 🧩 Components
Ready-to-use, customizable components:

```
Button       - Primary, secondary, ghost variants
Card         - Container with hover effects
Badge        - Status indicators
Section      - Page sections with titles
Grid         - Responsive grid layout
Container    - Content wrapper
```

### 🎨 Features
- ✅ Modern, clean design
- ✅ Fully responsive (mobile, tablet, desktop)
- ✅ Tailwind CSS styling
- ✅ TypeScript support
- ✅ Next.js 15 App Router
- ✅ Production-ready
- ✅ Zero config needed

---

## 🚀 What You Can Do

### Customize the Landing Page
Edit `src/app/page.tsx`:
- Change hero text and images
- Update features section
- Modify pricing tiers
- Add testimonials
- Update footer

### Add New Pages
```bash
mkdir -p src/app/your-page
# Create src/app/your-page/page.tsx
```

### Use Components
```tsx
import { Button, Card, Grid, Section } from '@/components/common';

export default function MyPage() {
  return (
    <Section title="My Section" subtitle="Description">
      <Grid cols={3}>
        <Card>
          <Button>Click me</Button>
        </Card>
      </Grid>
    </Section>
  );
}
```

### Change Branding
1. Update app name in `src/app/layout.tsx`
2. Change logo text in `src/app/page.tsx`
3. Modify colors in `src/app/globals.css`
4. Update environment variables in `.env.local`

### Connect to Backend
Uncomment in `.env.local`:
```
NEXT_PUBLIC_API_URL=https://localhost:8000
```

Then create your API client:
```tsx
import axios from 'axios';

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL,
});

export default api;
```

### Deploy to Production
- **Vercel** (Recommended): `vercel deploy`
- **Netlify**: `netlify deploy --prod`
- **Docker**: `docker build -t app . && docker run -p 3000:3000 app`

---

## 📁 Project Structure

```
src/
├── app/
│   ├── page.tsx              # Landing page
│   ├── login/page.tsx        # Login
│   ├── signup/page.tsx       # Signup
│   ├── layout.tsx            # Root layout
│   └── globals.css           # Global styles
│
└── components/
    └── common/               # Reusable components
        ├── Button.tsx
        ├── Card.tsx
        ├── Badge.tsx
        ├── Section.tsx
        ├── Grid.tsx
        ├── Container.tsx
        └── index.ts

package.json                  # Dependencies
tsconfig.json                 # TypeScript config
tailwind.config.ts           # Tailwind config
```

---

## 🛠️ Available Commands

```bash
# Development
npm run dev              # Start dev server (localhost:3000)
npm run build            # Build for production
npm run start            # Start production server

# Code Quality
npm run lint             # Run ESLint
npm run lint:fix         # Fix linting issues
npm run format           # Format code with Prettier
npm run type-check       # Check TypeScript

# Testing (optional)
npm run test             # Run tests
npm run test:e2e         # Run E2E tests
```

---

## 🎨 Customization Examples

### Example 1: Change Primary Color
**File:** `src/app/globals.css`
```css
/* Change all gray-900 to your color */
/* Or update Tailwind config */
```

### Example 2: Add New Feature Section
**File:** `src/app/page.tsx`
```tsx
<Section title="New Section" subtitle="Description">
  <Grid cols={3}>
    {/* Your content */}
  </Grid>
</Section>
```

### Example 3: Create Dashboard Page
```bash
mkdir -p src/app/dashboard
touch src/app/dashboard/page.tsx
```

```tsx
'use client';

import { Container } from '@/components/common';

export default function Dashboard() {
  return (
    <Container>
      <h1>Dashboard</h1>
      {/* Your content */}
    </Container>
  );
}
```

---

## 📚 Documentation

- **[TEMPLATE_GUIDE.md](./TEMPLATE_GUIDE.md)** - Full documentation
- **[QUICK_REFERENCE.md](./QUICK_REFERENCE.md)** - Quick cheatsheet
- **[Next.js Docs](https://nextjs.org/docs)**
- **[Tailwind Docs](https://tailwindcss.com/docs)**

---

## 🔗 Resources

### Learning
- [Next.js 15 Documentation](https://nextjs.org/docs)
- [React 19 Documentation](https://react.dev)
- [Tailwind CSS Guide](https://tailwindcss.com/docs)
- [TypeScript Handbook](https://www.typescriptlang.org/docs)

### Hosting
- [Vercel](https://vercel.com) - Recommended for Next.js
- [Netlify](https://netlify.com)
- [AWS Amplify](https://aws.amazon.com/amplify)
- [Docker Hub](https://hub.docker.com)

### Tools
- [VS Code](https://code.visualstudio.com)
- [Tailwind CSS IntelliSense](https://marketplace.visualstudio.com/items?itemName=bradlc.vscode-tailwindcss)
- [Prettier](https://prettier.io)
- [ESLint](https://eslint.org)

---

## 💡 Pro Tips

1. **Use Tailwind classes** instead of custom CSS for consistency
2. **Extract components** when you repeat code
3. **Use TypeScript** - it catches bugs before runtime
4. **Mobile first** - design for mobile, then scale up
5. **Keep components simple** - one responsibility each
6. **Responsive design** - use `md:`, `lg:` prefixes
7. **Test locally** before deploying

---

## 🐛 Troubleshooting

### Styles not showing?
```bash
rm -rf .next
npm run dev
```

### Port 3000 in use?
```bash
npm run dev -- -p 3001
```

### TypeScript errors?
```bash
npm run type-check
```

### Components not found?
Make sure paths in `tsconfig.json` include `@/` alias

---

## 📄 License

Free to use for personal and commercial projects.

---

## 🎯 Your Action Items

- [ ] Run `npm install`
- [ ] Run `npm run dev`
- [ ] Visit https://localhost:3000
- [ ] Edit the landing page
- [ ] Change the branding
- [ ] Add your content
- [ ] Deploy to production

---

## 🚀 Ready to Build?

Start with:
```bash
npm install
npm run dev
```

Then edit `src/app/page.tsx` to make it yours!

**Happy building!** 🎉
