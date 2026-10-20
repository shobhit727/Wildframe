import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // The brand red has two jobs, and they need different values.
        //
        // `brand` (DEFAULT / 500) is #e50914: it is a FILL colour -- button
        // backgrounds, borders, the focus ring. White on it is 4.79:1, so it
        // passes AA for text sitting on top of it.
        //
        // `brand-400` is the one to use for the brand colour as TEXT. #e50914
        // is only 4.11:1 against the page background (#0b0b0b) and 3.63:1
        // against a raised card (#1a1a1a), so it fails WCAG AA for normal
        // text in both places -- the wordmarks, the section eyebrows and the
        // red links were all failing. #f94c53 is 5.80:1 and 5.13:1 on the
        // same two backgrounds, so it passes everywhere in the app.
        //
        // `text-brand-400` for text, `bg-brand` / `border-brand` for fills.
        // `tests/color-contrast.test.ts` enforces the rule and re-checks the
        // ratios, so the value cannot silently drift back.
        // Muted text, for the second and third levels of the type hierarchy.
        //
        // The stock `gray-500` (#737373) and `zinc-500` (#71717a) tokens are
        // 3.7-4.2:1 against this app's dark surfaces, and `zinc-600`
        // (#52525b) is 2.3:1 -- all three fail WCAG 2.1 AA for normal text.
        // A real-browser audit of all 15 routes found 140 failing text nodes
        // and every one of them was one of those three tokens.
        //
        // These are the values the app should use instead. `muted` is 5.9:1 on
        // the lightest surface the app uses and 4.6:1 on a raised input, so it
        // clears AA everywhere including form fields; `muted-subtle` is 5.1:1
        // on a card, and is for the faintest labels on panels rather than on
        // inputs. The ramp still reads as a ramp: white > gray-400 (7.7:1) >
        // muted (5.9:1) > muted-subtle (5.1:1).
        //
        // The stock scales are deliberately left alone: `bg-zinc-600` is the
        // secondary button on the admin confirm dialog, and white text on
        // that background needs it to stay dark.
        //
        // `text-muted` for secondary text, `text-muted-subtle` for the
        // faintest labels. `tests/color-contrast.test.ts` enforces both the
        // rule and the ratios.
        muted: {
          DEFAULT: "#96969c",
          subtle: "#8a8a92",
        },
        brand: {
          DEFAULT: "#e50914",
          300: "#fd7a7e",
          400: "#f94c53",
          500: "#e50914",
        },
        primary: {
          50: "#f0f9ff",
          100: "#e0f2fe",
          200: "#bae6fd",
          300: "#7dd3fc",
          400: "#38bdf8",
          500: "#0ea5e9",
          600: "#0284c7",
          700: "#0369a1",
          800: "#075985",
          900: "#0c3d66",
          950: "#051e3e",
        },
        dark: {
          50: "#f5f5f1",
          100: "#e5e5e5",
          200: "#d2d2d2",
          300: "#b3b3b3",
          400: "#8c8c8c",
          500: "#737373",
          600: "#454545",
          700: "#333333",
          800: "#2f2f2f",
          850: "#1f1f1f",
          900: "#1a1a1a",
          950: "#141414",
        },
        netflix: {
          50: "#fff0f0",
          100: "#ffd6d7",
          200: "#fea9ac",
          300: "#fd7a7e",
          400: "#f94c53",
          500: "#e50914",
          600: "#d61212",
          700: "#b00710",
          800: "#8f060c",
          900: "#660409",
        },
        red: {
          50: "#fff0f0",
          100: "#ffd6d7",
          200: "#fea9ac",
          300: "#fd7a7e",
          400: "#f94c53",
          500: "#e50914",
          600: "#d61212",
          700: "#b00710",
          800: "#8f060c",
          900: "#660409",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)"],
        mono: ["var(--font-fira-code)"],
      },
      fontSize: {
        xs: ["0.75rem", { lineHeight: "1rem" }],
        sm: ["0.875rem", { lineHeight: "1.25rem" }],
        base: ["1rem", { lineHeight: "1.5rem" }],
        lg: ["1.125rem", { lineHeight: "1.75rem" }],
        xl: ["1.25rem", { lineHeight: "1.75rem" }],
        "2xl": ["1.5rem", { lineHeight: "2rem" }],
        "3xl": ["1.875rem", { lineHeight: "2.25rem" }],
        "4xl": ["2.25rem", { lineHeight: "2.5rem" }],
        "5xl": ["3rem", { lineHeight: "1" }],
        "6xl": ["3.75rem", { lineHeight: "1" }],
      },
      spacing: {
        "safe-top": "env(safe-area-inset-top)",
        "safe-bottom": "env(safe-area-inset-bottom)",
        "safe-left": "env(safe-area-inset-left)",
        "safe-right": "env(safe-area-inset-right)",
      },
      animation: {
        "fade-in": "fadeIn 0.3s ease-in-out",
        "slide-in": "slideIn 0.3s ease-out",
        "pulse-subtle": "pulseSubtle 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "shimmer": "shimmer 2s infinite",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideIn: {
          "0%": { transform: "translateY(10px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        pulseSubtle: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.8" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-1000px 0" },
          "100%": { backgroundPosition: "1000px 0" },
        },
      },
      backdropBlur: {
        xs: "2px",
      },
      transitionDuration: {
        DEFAULT: "200ms",
      },
    },
  },
  plugins: [
    require("@tailwindcss/typography"),
    require("@tailwindcss/forms"),
  ],
  safelist: [
    // For dynamic classes
    {
      pattern: /^(bg|text|border|ring)-(red|amber|green|blue|purple|pink)-(50|100|200|300|400|500|600|700|800|900)$/,
    },
  ],
};

export default config;
