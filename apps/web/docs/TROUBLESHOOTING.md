# 🐛 Troubleshooting Guide

Common issues and solutions.

## Startup Issues

### Port 3000 Already in Use

**Error:** `Error: listen EADDRINUSE: address already in use :::3000`

**Solution 1:** Use a different port
```bash
npm run dev -- -p 3001
```

**Solution 2:** Kill process using port
```bash
# macOS/Linux
lsof -i :3000
kill -9 <PID>

# Windows
netstat -ano | findstr :3000
taskkill /PID <PID> /F
```

---

### npm install Fails

**Error:** `npm ERR! code ERESOLVE`

**Solutions:**

1. Clear cache
   ```bash
   npm cache clean --force
   ```

2. Use legacy dependency resolution
   ```bash
   npm install --legacy-peer-deps
   ```

3. Delete node_modules and package-lock.json
   ```bash
   rm -rf node_modules package-lock.json
   npm install
   ```

---

### Node Version Issues

**Error:** `Node version not compatible`

**Solution:** Install correct Node version
```bash
# Check your Node version
node --version

# Install Node 18+
# Using nvm (macOS/Linux)
nvm install 18
nvm use 18

# Or download from nodejs.org
```

---

## Build Issues

### Build Fails

**Error:** `build failed`

**Solutions:**

1. Check TypeScript errors
   ```bash
   npm run type-check
   ```

2. Check linting errors
   ```bash
   npm run lint
   ```

3. Clear build cache
   ```bash
   rm -rf .next
   npm run build
   ```

---

### TypeScript Errors

**Error:** `error TS2307: Cannot find module`

**Solution:** Restart TypeScript server
- VS Code: `Cmd+Shift+P` → "TypeScript: Restart TS Server"
- Or restart dev server: `npm run dev`

---

## Development Issues

### Styles Not Loading

**Error:** CSS not applied to elements

**Solutions:**

1. Restart dev server
   ```bash
   # Stop: Ctrl+C
   npm run dev
   ```

2. Clear Next.js cache
   ```bash
   rm -rf .next
   npm run dev
   ```

3. Check Tailwind configuration
   - Verify `tailwind.config.ts` is correct
   - Check `tailwind.css` is imported

4. Hard refresh browser
   - Clear browser cache (DevTools → Storage → Clear)
   - `Ctrl+Shift+R` (hard refresh)

---

### Components Not Found

**Error:** `Module not found` or `export not found`

**Solutions:**

1. Check import path
   ```tsx
   // Wrong
   import { Button } from './common/Button';
   
   // Correct
   import { Button } from '@/components/common';
   ```

2. Verify file exists
   - Check `src/components/common/Button.tsx` exists
   - Check `src/components/common/index.ts` exports it

3. Restart dev server
   ```bash
   npm run dev
   ```

---

### Hot Module Replacement Not Working

**Issue:** Changes not reflecting without restart

**Solution:**

1. Check file is in `src/` directory
2. Restart dev server
3. Clear browser cache

---

## Runtime Issues

### Blank Page in Browser

**Error:** Page shows blank

**Solutions:**

1. Check browser console for errors
   - DevTools → Console
   - Look for red error messages

2. Check Network tab for failed requests
   - DevTools → Network
   - See which requests failed

3. Clear localStorage
   ```javascript
   localStorage.clear();
   location.reload();
   ```

4. Check page source
   - Right-click → View Page Source
   - Should see HTML content

---

### Button/Link Not Working

**Issue:** Click handler not firing

**Solutions:**

1. Ensure 'use client' directive
   ```tsx
   'use client';
   
   import { Button } from '@/components/common';
   ```

2. Check onClick handler
   ```tsx
   <Button onClick={() => console.log('clicked')}>
     Click me
   </Button>
   ```

3. Verify component is interactive
   - Not wrapped in `<a>` tag
   - Not disabled

---

### Form Not Submitting

**Issue:** Form submission not working

**Solutions:**

1. Check form structure
   ```tsx
   <form onSubmit={handleSubmit}>
     <input />
     <button type="submit">Submit</button>
   </form>
   ```

2. Verify handler
   ```tsx
   const handleSubmit = (e: React.FormEvent) => {
     e.preventDefault();  // Prevent default behavior
     // Your code
   };
   ```

3. Check for JavaScript errors in console

---

## API Integration Issues

### API Requests Failing

**Error:** `Failed to fetch` or CORS error

**Solutions:**

1. Check API URL
   ```
   NEXT_PUBLIC_API_URL=http://localhost:8000
   ```

2. Verify backend is running
   ```bash
   curl http://localhost:8000/health
   ```

3. Check CORS headers
   - Backend should allow frontend origin
   - Or use proxy

4. Check request in Network tab
   - DevTools → Network
   - Look for failed requests
   - Check response

---

### 401 Unauthorized Error

**Issue:** API requests return 401

**Solutions:**

1. Verify token is set
   ```javascript
   console.log(localStorage.getItem('authToken'));
   ```

2. Check token is valid
   - May have expired
   - Refresh token needed

3. Verify Authorization header is sent
   ```typescript
   // Check in Network tab
   // Should see: Authorization: Bearer <token>
   ```

---

### CORS Error

**Error:** `Access to XMLHttpRequest blocked by CORS policy`

**Solutions:**

1. Configure backend CORS
   ```python
   # FastAPI example
   from fastapi.middleware.cors import CORSMiddleware
   
   app.add_middleware(
       CORSMiddleware,
       allow_origins=["http://localhost:3000"],
       allow_credentials=True,
       allow_methods=["*"],
       allow_headers=["*"],
   )
   ```

2. Or use Next.js API route as proxy
   ```tsx
   // src/app/api/proxy/[...path]/route.ts
   export async function GET(request) {
     // Proxy request to backend
   }
   ```

---

## Performance Issues

### Slow Build Time

**Solutions:**

1. Check what's taking time
   ```bash
   npm run build -- --debug
   ```

2. Analyze bundle
   ```bash
   ANALYZE=true npm run build
   ```

3. Optimize imports
   - Use dynamic imports for large components
   - Split code into chunks

---

### Slow Page Load

**Solutions:**

1. Optimize images
   - Use Next.js Image component
   - Compress images

2. Enable caching
   - Set proper Cache-Control headers

3. Use CDN
   - Serve static files from CDN
   - Reduce server load

4. Check lighthouse score
   ```bash
   npm run build
   npm run start
   # Open DevTools → Lighthouse → Generate report
   ```

---

## Deployment Issues

### Deployment Fails

**Solutions:**

1. Check build locally
   ```bash
   npm run build
   ```

2. Check environment variables
   - All required variables set
   - Correct values for production

3. Check logs in deployment platform
   - Vercel: Deployments tab
   - Netlify: Deploys tab

4. Try redeploying
   ```bash
   # Vercel
   vercel --prod
   
   # Netlify
   netlify deploy --prod
   ```

---

### Site Shows Old Version

**Issue:** Changes not reflected in production

**Solutions:**

1. Hard refresh browser
   - `Ctrl+Shift+R`
   - Or clear browser cache

2. Redeploy
   - Re-push to Git
   - Or manual redeploy

3. Purge cache
   - Vercel: Project Settings → Purge Cache
   - Netlify: Deploys → Trigger deploy

---

## Getting Help

1. **Check logs**
   - Browser console (F12)
   - Terminal output
   - Deployment platform logs

2. **Search online**
   - Google the error message
   - Check Stack Overflow
   - Check GitHub issues

3. **Read documentation**
   - [Next.js Docs](https://nextjs.org/docs)
   - [React Docs](https://react.dev)
   - [Tailwind Docs](https://tailwindcss.com/docs)

4. **Ask for help**
   - GitHub Discussions
   - Stack Overflow
   - Community Discord servers

---

## Still Stuck?

1. Clear everything and restart
   ```bash
   rm -rf node_modules .next package-lock.json
   npm install
   npm run dev
   ```

2. Check git status
   ```bash
   git status
   git log --oneline -5
   ```

3. If all else fails, create minimal reproduction
   - Create new test file
   - Isolate the issue
   - Test in isolation
