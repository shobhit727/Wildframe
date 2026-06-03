# 🚀 Deployment Guide

Learn how to deploy your app to production.

## Deployment Options

### 1. Vercel (Recommended)

**Pros:**
- Optimized for Next.js
- Automatic deployments from Git
- Edge functions
- Free tier available

**Steps:**

1. Sign up at [vercel.com](https://vercel.com)
2. Connect your Git repository
3. Vercel auto-detects Next.js and configures build
4. Add environment variables in Vercel dashboard
5. Deploy!

```bash
# Or use Vercel CLI
npm i -g vercel
vercel
```

### 2. Netlify

**Pros:**
- Easy to use
- Good free tier
- Git integration
- Serverless functions

**Steps:**

1. Build your app
   ```bash
   npm run build
   ```

2. Deploy to Netlify
   ```bash
   npm i -g netlify-cli
   netlify deploy --prod --dir=.next
   ```

Or connect Git repo at [netlify.com](https://netlify.com)

### 3. Docker

**Pros:**
- Full control
- Can run anywhere
- Reproducible

**Create Dockerfile:**

```dockerfile
FROM node:18-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install --production

COPY .next ./
COPY public ./public

EXPOSE 3000

CMD ["npm", "start"]
```

**Build and run:**

```bash
docker build -t myapp .
docker run -p 3000:3000 myapp
```

### 4. AWS, GCP, Azure

Each platform offers Next.js hosting:
- AWS Amplify
- Google Cloud Run
- Azure App Service

Check their documentation for specific steps.

---

## Pre-Deployment Checklist

- [ ] Build successfully: `npm run build`
- [ ] No TypeScript errors: `npm run type-check`
- [ ] No lint errors: `npm run lint`
- [ ] Tests pass: `npm run test`
- [ ] Environment variables set
- [ ] Images optimized
- [ ] No console errors
- [ ] Responsive design tested
- [ ] Forms tested
- [ ] API endpoints working

---

## Environment Variables

### Development (.env.local)
```
NEXT_PUBLIC_APP_NAME=MyApp
NEXT_PUBLIC_APP_URL=http://localhost:3000
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### Production

Update in deployment platform:

**Vercel:**
1. Go to Project Settings → Environment Variables
2. Add each variable

**Netlify:**
1. Go to Site Settings → Build & deploy → Environment
2. Add each variable

**Values to update:**
```
NEXT_PUBLIC_APP_NAME=MyApp
NEXT_PUBLIC_APP_URL=https://myapp.com
NEXT_PUBLIC_API_URL=https://api.myapp.com
```

---

## Performance Optimization

### Image Optimization
- Use Next.js Image component
- Serve WebP format
- Lazy load images

### Code Splitting
```tsx
import dynamic from 'next/dynamic';

const HeavyComponent = dynamic(() => import('./HeavyComponent'), {
  loading: () => <p>Loading...</p>,
});
```

### Bundle Analysis
```bash
npm install --save-dev @next/bundle-analyzer
```

Update `next.config.ts`:
```typescript
const withBundleAnalyzer = require('@next/bundle-analyzer')({
  enabled: process.env.ANALYZE === 'true',
});

module.exports = withBundleAnalyzer({
  // Your Next.js config
});
```

Run:
```bash
ANALYZE=true npm run build
```

### Caching
```tsx
export const revalidate = 60; // ISR - revalidate every 60 seconds
```

---

## Monitoring & Logs

### Vercel
- Analytics in dashboard
- Real-time logs
- Error tracking

### Netlify
- Deploy logs
- Function logs
- Analytics

### Application Monitoring
Consider adding:
- [Sentry](https://sentry.io) - Error tracking
- [LogRocket](https://logrocket.com) - Session replay
- [Datadog](https://datadoghq.com) - Application monitoring

---

## Custom Domain

### Vercel
1. Go to Project Settings → Domains
2. Add your domain
3. Update DNS records at your registrar

### Netlify
1. Go to Site Settings → Domain management
2. Add custom domain
3. Follow DNS instructions

---

## SSL/TLS Certificate

Most platforms provide free SSL:
- Vercel: Automatic
- Netlify: Automatic
- AWS: Use AWS Certificate Manager
- Google Cloud: Use Google-managed certificates

---

## Deployment Commands

### Build
```bash
npm run build
```

### Start Production Server
```bash
npm run start
```

### Preview Build Locally
```bash
npm run build
npm run start
```

---

## Troubleshooting Deployments

### Build Fails
```bash
# Check build locally
npm run build

# Check for errors
npm run type-check
npm run lint
```

### Slow Performance
- Enable caching headers
- Optimize images
- Enable compression
- Use CDN

### Environment Variables Not Working
- Ensure variables are set in deployment platform
- Prefix with NEXT_PUBLIC_ for client-side
- Restart deployment after changing variables

### Blank Page
- Check browser console for errors
- Check deployment logs
- Verify API endpoint is correct

---

## Security Best Practices

1. **Never commit secrets**
   - Use environment variables for API keys
   - Add `.env.local` to `.gitignore`

2. **HTTPS only**
   - All production deployments should use HTTPS

3. **CORS configuration**
   - Set proper CORS headers on backend
   - Only allow your frontend domain

4. **Input validation**
   - Validate all user inputs
   - Sanitize before sending to API

5. **Rate limiting**
   - Implement on backend
   - Prevent abuse

---

## Rollback

### Vercel
- Deployments tab shows all versions
- Click "Promote to Production" on previous version

### Netlify
- Go to "Deploys"
- Click "Publish deploy" on previous version

### Manual
- Revert commits in Git
- Redeploy

---

## Next Steps

1. ✅ Build: `npm run build`
2. ✅ Test: `npm run test`
3. ✅ Deploy to staging first
4. ✅ Test in production environment
5. ✅ Configure domain
6. ✅ Set up monitoring
7. ✅ Deploy to production

---

## Resources

- [Next.js Deployment](https://nextjs.org/docs/deployment)
- [Vercel Docs](https://vercel.com/docs)
- [Netlify Docs](https://docs.netlify.com)
- [Docker Documentation](https://docs.docker.com)
