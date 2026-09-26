'use client';

import { SignupForm } from '@/components/auth/SignupForm';

// Keep the route as a thin composition layer so the tested signup form is the live form.
export default function SignupPage() {
  return <SignupForm />;
}
