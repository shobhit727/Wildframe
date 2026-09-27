'use client';

import { LoginForm } from '@/components/auth/LoginForm';

// Keep the route as a thin composition layer so the tested auth form is the live form.
export default function LoginPage() {
  return <LoginForm />;
}
