# 🧩 Components Guide

Learn how to use all available components.

## Button Component

Customizable button with multiple variants and sizes.

### Import
```tsx
import { Button } from '@/components/common';
```

### Basic Usage
```tsx
<Button>Click me</Button>
```

### Variants
```tsx
<Button variant="primary">Primary</Button>
<Button variant="secondary">Secondary</Button>
<Button variant="ghost">Ghost</Button>
```

**Variants:**
- `primary` - Filled button (dark background)
- `secondary` - Outlined button
- `ghost` - Text only button

### Sizes
```tsx
<Button size="sm">Small</Button>
<Button size="md">Medium</Button>
<Button size="lg">Large</Button>
```

**Sizes:**
- `sm` - Small (px-4 py-2)
- `md` - Medium (px-6 py-3)
- `lg` - Large (px-8 py-4)

### With Click Handler
```tsx
<Button onClick={() => console.log('Clicked!')}>
  Click me
</Button>
```

### Disabled State
```tsx
<Button disabled>Disabled</Button>
```

### Custom Styling
```tsx
<Button className="rounded-full w-full">
  Custom Button
</Button>
```

---

## Card Component

Reusable container for content with optional hover effects.

### Import
```tsx
import { Card } from '@/components/common';
```

### Basic Usage
```tsx
<Card>
  <h3>Card Title</h3>
  <p>Card content goes here</p>
</Card>
```

### With Hover
```tsx
<Card hover>
  <h3>Hover me</h3>
  <p>This card has hover effects</p>
</Card>
```

### Without Hover
```tsx
<Card hover={false}>
  <h3>No hover</h3>
  <p>This card doesn't have hover effects</p>
</Card>
```

### Custom Padding
```tsx
<Card className="p-12">
  Large padding
</Card>
```

### With Components Inside
```tsx
<Card>
  <h3>Feature Card</h3>
  <p>Description</p>
  <Button>Learn More</Button>
</Card>
```

---

## Badge Component

Status indicators for labeling content.

### Import
```tsx
import { Badge } from '@/components/common';
```

### Basic Usage
```tsx
<Badge>New</Badge>
```

### Variants
```tsx
<Badge variant="default">Default</Badge>
<Badge variant="success">Success</Badge>
<Badge variant="warning">Warning</Badge>
<Badge variant="error">Error</Badge>
```

**Variants:**
- `default` - Gray background
- `success` - Green background
- `warning` - Amber background
- `error` - Red background

### Use Cases
```tsx
{/* Mark new features */}
<Badge variant="success">New Feature</Badge>

{/* Show status */}
<Badge variant="warning">In Progress</Badge>

{/* Mark errors */}
<Badge variant="error">Error</Badge>

{/* General labels */}
<Badge>Free Tier</Badge>
```

---

## Section Component

Container for page sections with optional title and subtitle.

### Import
```tsx
import { Section } from '@/components/common';
```

### Basic Usage
```tsx
<Section>
  {/* Your content */}
</Section>
```

### With Title
```tsx
<Section title="Features">
  {/* Your content */}
</Section>
```

### With Title and Subtitle
```tsx
<Section title="Features" subtitle="Everything you need">
  {/* Your content */}
</Section>
```

### Dark Background
```tsx
<Section title="Dark Section" dark>
  {/* Content on dark background */}
</Section>
```

### Custom Styling
```tsx
<Section className="py-32" title="Custom">
  {/* Content with custom padding */}
</Section>
```

### Typical Structure
```tsx
<Section title="Our Features" subtitle="What we offer">
  <Container>
    <Grid cols={3}>
      <Card>Feature 1</Card>
      <Card>Feature 2</Card>
      <Card>Feature 3</Card>
    </Grid>
  </Container>
</Section>
```

---

## Grid Component

Responsive grid layout for arranging content.

### Import
```tsx
import { Grid } from '@/components/common';
```

### Basic Usage
```tsx
<Grid>
  <div>Item 1</div>
  <div>Item 2</div>
  <div>Item 3</div>
</Grid>
```

### Columns
```tsx
<Grid cols={1}>One column</Grid>
<Grid cols={2}>Two columns (responsive)</Grid>
<Grid cols={3}>Three columns (responsive)</Grid>
<Grid cols={4}>Four columns (responsive)</Grid>
```

**Responsive Behavior:**
- `cols={1}` - Always 1 column
- `cols={2}` - 1 column (mobile), 2 columns (desktop)
- `cols={3}` - 1 column (mobile), 2 columns (tablet), 3 columns (desktop)
- `cols={4}` - 1 column (mobile), 2 columns (tablet), 4 columns (desktop)

### Gap (Spacing)
```tsx
<Grid gap="sm">Small gap</Grid>
<Grid gap="md">Medium gap</Grid>
<Grid gap="lg">Large gap</Grid>
```

**Gap Sizes:**
- `sm` - 16px (gap-4)
- `md` - 32px (gap-8)
- `lg` - 48px (gap-12)

### With Cards
```tsx
<Grid cols={3} gap="md">
  <Card>Item 1</Card>
  <Card>Item 2</Card>
  <Card>Item 3</Card>
</Grid>
```

---

## Container Component

Content wrapper for consistent max-width and padding.

### Import
```tsx
import { Container } from '@/components/common';
```

### Basic Usage
```tsx
<Container>
  <h1>Your content here</h1>
  <p>Automatically centered and sized</p>
</Container>
```

### With Custom Styling
```tsx
<Container className="py-12">
  <h1>Custom padding</h1>
</Container>
```

### Nested Containers
```tsx
<Container>
  <Section>
    <Container>
      <p>Can be nested</p>
    </Container>
  </Section>
</Container>
```

### Typical Usage
```tsx
<Section title="My Section">
  <Container>
    <Grid cols={3}>
      <Card>Content 1</Card>
      <Card>Content 2</Card>
      <Card>Content 3</Card>
    </Grid>
  </Container>
</Section>
```

---

## Combining Components

### Example 1: Feature Section
```tsx
import { Section, Container, Grid, Card, Badge, Button } from '@/components/common';

export default function Features() {
  return (
    <Section title="Features" subtitle="What we offer">
      <Container>
        <Grid cols={3}>
          <Card>
            <Badge variant="success">New</Badge>
            <h3>Feature 1</h3>
            <p>Description of feature</p>
            <Button>Learn More</Button>
          </Card>
          <Card>
            <h3>Feature 2</h3>
            <p>Description of feature</p>
            <Button>Learn More</Button>
          </Card>
          <Card>
            <h3>Feature 3</h3>
            <p>Description of feature</p>
            <Button>Learn More</Button>
          </Card>
        </Grid>
      </Container>
    </Section>
  );
}
```

### Example 2: Pricing Table
```tsx
<Section title="Pricing" subtitle="Choose your plan">
  <Container>
    <Grid cols={3}>
      <Card hover={false}>
        <h3>Starter</h3>
        <p className="text-2xl font-bold">Free</p>
        <ul>
          <li>Feature 1</li>
          <li>Feature 2</li>
        </ul>
        <Button variant="secondary">Get Started</Button>
      </Card>
      <Card hover>
        <Badge variant="success">Popular</Badge>
        <h3>Pro</h3>
        <p className="text-2xl font-bold">$19/mo</p>
        <ul>
          <li>All Starter features</li>
          <li>Feature 3</li>
          <li>Feature 4</li>
        </ul>
        <Button>Choose Plan</Button>
      </Card>
      <Card>
        <h3>Enterprise</h3>
        <p className="text-2xl font-bold">Custom</p>
        <ul>
          <li>All Pro features</li>
          <li>Dedicated support</li>
          <li>Custom integration</li>
        </ul>
        <Button variant="secondary">Contact Sales</Button>
      </Card>
    </Grid>
  </Container>
</Section>
```

---

## Props Reference

### Button Props
```tsx
interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost';  // default: 'primary'
  size?: 'sm' | 'md' | 'lg';                    // default: 'md'
  children: React.ReactNode;
}
```

### Card Props
```tsx
interface CardProps {
  children: React.ReactNode;
  className?: string;
  hover?: boolean;  // default: true
}
```

### Badge Props
```tsx
interface BadgeProps {
  children: React.ReactNode;
  variant?: 'default' | 'success' | 'warning' | 'error';  // default: 'default'
}
```

### Section Props
```tsx
interface SectionProps {
  children: React.ReactNode;
  title?: string;
  subtitle?: string;
  className?: string;
  dark?: boolean;  // default: false
}
```

### Grid Props
```tsx
interface GridProps {
  children: React.ReactNode;
  cols?: 1 | 2 | 3 | 4;              // default: 3
  gap?: 'sm' | 'md' | 'lg';          // default: 'md'
}
```

### Container Props
```tsx
interface ContainerProps {
  children: React.ReactNode;
  className?: string;
}
```

---

## Tips

1. **Always import from @/components/common** for consistency
2. **Combine components** to create complex layouts
3. **Pass className** for custom styling when needed
4. **Use semantic HTML** inside components
5. **Mobile first** - components are responsive by default
