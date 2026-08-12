# 🚀 Getting Started

## Prerequisites

- **Node.js 18+** - [Download](https://nodejs.org)
- **npm** - Comes with Node.js
- **Git** - [Download](https://git-scm.com)
- **Text Editor** - VS Code recommended

## Installation

### 1. Navigate to Project
```bash
cd /home/phoenix/Desktop/wildframe/apps/web
```

### 2. Install Dependencies
```bash
npm install
```

This installs all required packages from `package.json`.

### 3. Setup Environment
```bash
cp .env.local.example .env.local
```

Edit `.env.local` to customize:
```
NEXT_PUBLIC_APP_NAME=YourApp
NEXT_PUBLIC_APP_URL=https://localhost:3000
# NEXT_PUBLIC_API_URL=https://localhost:8000  # Uncomment for backend
```

### 4. Start Development Server
```bash
npm run dev
```

Output:
```
> next dev

  ▲ Next.js 15.0.0
  - Local:        https://localhost:3000
  - Environments: .env.local

✓ Ready in 2.5s
```

### 5. Open in Browser
Visit: **https://localhost:3000**

---

## Project Structure

```
.
├── src/
│   ├── app/                    # Pages (Next.js App Router)
│   │   ├── page.tsx           # Landing page (/)
│   │   ├── login/
│   │   │   └── page.tsx       # Login page (/login)
│   │   ├── signup/
│   │   │   └── page.tsx       # Signup page (/signup)
│   │   ├── layout.tsx         # Root layout
│   │   ├── globals.css        # Global styles
│   │   └── favicon.ico        # Favicon
│   │
│   ├── components/
│   │   └── common/            # Reusable components
│   │       ├── Button.tsx     # Button component
│   │       ├── Card.tsx       # Card component
│   │       ├── Badge.tsx      # Badge component
│   │       ├── Section.tsx    # Section component
│   │       ├── Grid.tsx       # Grid component
│   │       ├── Container.tsx  # Container component
│   │       └── index.ts       # Exports
│   │
│   ├── api/                   # API integration (optional)
│   ├── stores/                # State management (optional)
│   ├── hooks/                 # Custom hooks (optional)
│   └── types/                 # TypeScript types (optional)
│
├── public/                    # Static assets
├── docs/                      # Documentation
├── .env.local.example         # Environment template
├── .gitignore                 # Git ignore rules
├── package.json               # Dependencies
├── tsconfig.json              # TypeScript config
├── tailwind.config.ts         # Tailwind config
├── next.config.ts             # Next.js config
└── README_TEMPLATE.md         # Template README
```

---

## Common Tasks

### Start Development
```bash
npm run dev
```

### Build for Production
```bash
npm run build
npm run start
```

### Run Linter
```bash
npm run lint
npm run lint:fix
```

### Format Code
```bash
npm run format
```

### Type Check
```bash
npm run type-check
```

### Run Tests
```bash
npm run test
npm run test:e2e
```

---

## Navigation

- [Customization Guide](./CUSTOMIZATION.md) - How to customize the template
- [Components Guide](./COMPONENTS.md) - How to use components
- [API Integration](./API_INTEGRATION.md) - Connect to backend
- [Deployment Guide](./DEPLOYMENT.md) - Deploy to production
- [Troubleshooting](./TROUBLESHOOTING.md) - Common issues and solutions

---

## Next Steps

1. ✅ Install and run the project
2. 📝 [Customize the landing page](./CUSTOMIZATION.md)
3. 🧩 [Use reusable components](./COMPONENTS.md)
4. 🔗 [Connect to backend API](./API_INTEGRATION.md)
5. 🚀 [Deploy to production](./DEPLOYMENT.md)

---

## Need Help?

- Check [Troubleshooting](./TROUBLESHOOTING.md) guide
- Read [TEMPLATE_GUIDE.md](../TEMPLATE_GUIDE.md) in root
- Check [QUICK_REFERENCE.md](../QUICK_REFERENCE.md) for quick lookup
- See [Next.js Docs](https://nextjs.org/docs)
