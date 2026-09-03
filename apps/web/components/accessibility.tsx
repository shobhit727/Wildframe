"use client";
export function AccessibilityProvider({children}:{children:React.ReactNode}){ return <div role="main" aria-live="polite">{children}</div> }
export function Captions({text}:{text:string}){ return <div aria-label="Captions">{text}</div> }
