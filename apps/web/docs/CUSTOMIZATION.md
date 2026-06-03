# 🎨 Customization Guide

Learn how to customize the template to match your brand and needs.

## Changing App Name

### 1. Update Environment
Edit `.env.local`:
```
NEXT_PUBLIC_APP_NAME=MyAwesomeApp
```

### 2. Update Layout Metadata
Edit `src/app/layout.tsx`:
```tsx
export const metadata: Metadata = {
  title: 'MyAwesomeApp - Modern Platform',
  description: 'Your app description',
};
```

### 3. Update Landing Page
Edit `src/app/page.tsx` and change the logo text:
```tsx
<div className="text-2xl font-bold text-gray-900">MyAwesomeApp</div>
```

---

## Changing Colors

### Method 1: Tailwind Classes (Easiest)
Edit components and change color classes:
```tsx
// Change from: bg-gray-900 text-white
// To: bg-blue-600 text-white
<button className="bg-blue-600 text-white px-6 py-3 rounded-lg">
  Click me
</button>
```

### Method 2: Global Styles
Edit `src/app/globals.css`:
```css
/* Define custom colors */
:root {
  --primary: #1f2937;    /* Gray-900 */
  --secondary: #6b7280;  /* Gray-500 */
  --accent: #ef4444;     /* Red-500 */
}
```

### Method 3: Tailwind Config
Edit `tailwind.config.ts`:
```js
module.exports = {
  theme: {
    extend: {
      colors: {
        primary: '#1f2937',
        secondary: '#6b7280',
        accent: '#ef4444',
      },
    },
  },
};
```

Then use in components:
```tsx
<button className="bg-primary text-white">Primary Button</button>
```

### Common Color Palette
```
Primary:    #1f2937 (Gray-900)
Secondary:  #6b7280 (Gray-500)
Accent:     #ef4444 (Red)
Success:    #10b981 (Green)
Warning:    #f59e0b (Amber)
Error:      #ef4444 (Red)
Background: #ffffff (White)
Text:       #111827 (Gray-950)
```

---

## Updating Landing Page

### Hero Section
Edit `src/app/page.tsx`:
```tsx
<h1 className="text-6xl font-bold text-gray-900 mb-6">
  Build faster, deploy smarter  {/* Change this */}
</h1>
<p className="text-xl text-gray-600 mb-8">
  Everything you need...  {/* Change this */}
</p>
```

### Features Section
```tsx
{[
  {
    icon: '⚡',
    title: 'Your Feature Title',
    desc: 'Your feature description',
  },
  // Add more features
].map((feature, i) => (
  // Component renders here
))}
```

### Pricing Section
```tsx
{[
  {
    name: 'Starter',
    price: 'Free',
    features: ['Feature 1', 'Feature 2'],
  },
  // Update pricing tiers
].map((plan, i) => (
  // Component renders here
))}
```

### Testimonials Section
```tsx
{[
  {
    quote: 'Your testimonial text',
    author: 'Author Name',
    role: 'Their Role',
  },
  // Add testimonials
].map((testimonial, i) => (
  // Component renders here
))}
```

---

## Adding New Sections

### Create a New Section
1. Add to `src/app/page.tsx`:
```tsx
import { Section, Container, Grid, Card } from '@/components/common';

<Section title="My Section" subtitle="Description" dark={false}>
  <Container>
    <Grid cols={3}>
      <Card>
        <h3>Item 1</h3>
        <p>Description</p>
      </Card>
      <Card>
        <h3>Item 2</h3>
        <p>Description</p>
      </Card>
      <Card>
        <h3>Item 3</h3>
        <p>Description</p>
      </Card>
    </Grid>
  </Container>
</Section>
```

### Dark vs Light Background
```tsx
// Light background (white)
<Section title="Section">...</Section>

// Dark background (gray-900)
<Section title="Section" dark>...</Section>
```

---

## Changing Fonts

### Using System Fonts
Edit `src/app/globals.css`:
```css
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto',
    'Oxygen', 'Ubuntu', 'Cantarell', sans-serif;
}
```

### Using Google Fonts
1. Add to `src/app/layout.tsx`:
```tsx
import { Poppins, Inter } from 'next/font/google';

const poppins = Poppins({
  subsets: ['latin'],
  weight: ['400', '600', '700'],
});

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={poppins.className}>
      <body>{children}</body>
    </html>
  );
}
```

---

## Adding Images

### Hero Image
```tsx
import Image from 'next/image';

<div className="relative h-96 w-full rounded-lg overflow-hidden">
  <Image
    src="/hero-image.jpg"
    alt="Hero"
    fill
    className="object-cover"
  />
</div>
```

### Feature Icons
Replace emoji with images:
```tsx
// Instead of: icon: '⚡'
// Use:
<Image src="/icons/lightning.svg" alt="Lightning" width={32} height={32} />
```

### Add Images to Public Folder
1. Create `public/images/` folder
2. Add image files
3. Reference in components:
```tsx
<img src="/images/my-image.jpg" alt="Description" />
// or
<Image src="/images/my-image.jpg" alt="Description" width={200} height={200} />
```

---

## Customizing Navigation

Edit navigation in `src/app/page.tsx`:
```tsx
<nav className="flex gap-8">
  <a href="#features">Features</a>
  <a href="#pricing">Pricing</a>
  <a href="#docs">Docs</a>
  <a href="#about">About</a>  {/* Add new link */}
</nav>
```

Add corresponding section:
```tsx
<section id="about" className="py-20">
  <h2>About Us</h2>
  {/* Your content */}
</section>
```

---

## Customizing Footer

Edit footer in `src/app/page.tsx`:
```tsx
<footer className="bg-white border-t border-gray-200 py-12">
  <div className="grid md:grid-cols-4 gap-8">
    {/* Column 1 */}
    <div>
      <h4>Product</h4>
      <ul>
        <li><a href="#">Your Link</a></li>
      </ul>
    </div>
    {/* More columns */}
  </div>
</footer>
```

---

## Customizing Forms

### Login Form
Edit `src/app/login/page.tsx`:
```tsx
<input
  type="email"
  placeholder="your@email.com"  {/* Change placeholder */}
  className="w-full px-4 py-3 border border-gray-300 rounded-lg"
/>
```

### Add Form Validation
```tsx
const [errors, setErrors] = useState({});

const handleSubmit = (e) => {
  e.preventDefault();
  const newErrors = {};
  
  if (!email) newErrors.email = 'Email is required';
  if (!password) newErrors.password = 'Password is required';
  
  if (Object.keys(newErrors).length > 0) {
    setErrors(newErrors);
    return;
  }
  
  // Submit form
};
```

---

## Component Customization

### Button Variants
```tsx
import { Button } from '@/components/common';

// Primary (filled)
<Button variant="primary">Primary</Button>

// Secondary (outlined)
<Button variant="secondary">Secondary</Button>

// Ghost (text only)
<Button variant="ghost">Ghost</Button>
```

### Button Sizes
```tsx
<Button size="sm">Small</Button>
<Button size="md">Medium</Button>
<Button size="lg">Large</Button>
```

### Custom Styling
```tsx
<Button className="rounded-full">Custom Shape</Button>
```

---

## Tips & Best Practices

1. **Keep it consistent** - Use the same colors throughout
2. **Mobile first** - Design for mobile, then scale up
3. **Test responsive** - Check on phone, tablet, desktop
4. **Use components** - Reuse components instead of repeating code
5. **Semantic HTML** - Use proper HTML tags
6. **Accessibility** - Add alt text to images
7. **Performance** - Optimize images, lazy load

---

## Next Steps

- [Component Guide](./COMPONENTS.md) - Learn all components
- [API Integration](./API_INTEGRATION.md) - Connect to backend
- [Deployment Guide](./DEPLOYMENT.md) - Deploy your app
