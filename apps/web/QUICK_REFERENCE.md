# 🚀 Template Quick Reference

## Start Development
\`\`\`bash
npm install
npm run dev
\`\`\`
Open http://localhost:3000

## Project Structure
```
src/app/
├── page.tsx              # Landing page (hero, features, pricing)
├── login/page.tsx        # Login form
├── signup/page.tsx       # Signup form
└── layout.tsx            # Root layout

src/components/common/
├── Button.tsx            # Customizable button
├── Card.tsx              # Card container
├── Badge.tsx             # Status badge
├── Section.tsx           # Section wrapper
├── Grid.tsx              # Grid layout
└── Container.tsx         # Container wrapper
```

## Most Common Edits

### Change Brand Colors
Edit `src/app/globals.css` or use Tailwind classes

### Update Landing Content
Edit `src/app/page.tsx` - modify hero, features, pricing sections

### Add New Page
\`\`\`bash
mkdir -p src/app/your-page
touch src/app/your-page/page.tsx
\`\`\`

### Use Components
\`\`\`tsx
import { Button, Card, Section, Grid } from '@/components/common';

<Section title="Your Title" subtitle="Subtitle here">
  <Grid cols={3}>
    <Card><Button>Click me</Button></Card>
  </Grid>
</Section>
\`\`\`

## Environment Variables
\`\`\`
NEXT_PUBLIC_APP_NAME=YourApp
NEXT_PUBLIC_APP_URL=http://localhost:3000
NEXT_PUBLIC_API_URL=http://localhost:8000  # Optional
\`\`\`

## Available Scripts
```
npm run dev         # Start dev server
npm run build       # Build for production
npm run lint        # Check code quality
npm run format      # Format code
npm run type-check  # TypeScript check
```

## Deployment
- **Vercel**: `vercel deploy`
- **Netlify**: `netlify deploy --prod`
- **Docker**: `docker build -t app . && docker run -p 3000:3000 app`

## Color Palette
- Primary: Gray-900 (customize in Tailwind config)
- Background: White
- Text: Gray-900
- Borders: Gray-200
- Hover: Gray-50, Gray-800

## Component Variants

### Button
- Variant: `primary` | `secondary` | `ghost`
- Size: `sm` | `md` | `lg`

### Grid
- Cols: `1` | `2` | `3` | `4`
- Gap: `sm` | `md` | `lg`

### Section
- `dark`: Boolean (dark/light background)
- `title`: Section heading
- `subtitle`: Section subheading

## Tips
1. Use `md:`, `lg:` prefixes for responsive design
2. All components accept `className` for custom styles
3. Tailwind is pre-configured and ready to use
4. Clean and modify landing page for your needs
5. Components are composable - build UI from them

---

For detailed guide, see **TEMPLATE_GUIDE.md**
