import type { Config } from "tailwindcss";

/**
 * Tailwind config — ChatLens dark-first design system.
 *
 * Design tokens:
 *   - Body / chrome:   deep charcoal (#0f0f13 .. #1a1a24)
 *   - Cards / panels:  #161620 with subtle indigo border on hover
 *   - Accent:          electric indigo #6366f1 with violet glow
 *   - Type:            Syne (display) + DM Sans (body), wired via CSS vars
 *
 * The CSS-variable indirection (`hsl(var(--primary))`) lets us re-skin
 * without rebuilding Tailwind, and keeps Shadcn primitives compatible
 * with our palette.
 */
const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
          glow: "hsl(var(--primary-glow))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        warning: {
          DEFAULT: "hsl(var(--warning))",
          foreground: "hsl(var(--warning-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
          elevated: "hsl(var(--card-elevated))",
        },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar))",
          foreground: "hsl(var(--sidebar-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          "accent-foreground": "hsl(var(--sidebar-accent-foreground))",
          border: "hsl(var(--sidebar-border))",
        },
        chart: {
          1: "hsl(var(--chart-1))",
          2: "hsl(var(--chart-2))",
          3: "hsl(var(--chart-3))",
          4: "hsl(var(--chart-4))",
          5: "hsl(var(--chart-5))",
        },
      },
      fontFamily: {
        // Body. Wired via next/font in app/layout.tsx.
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        // Display. Used on hero, section headers, large metric labels.
        display: ["var(--font-display)", "var(--font-sans)", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xl: "calc(var(--radius) + 4px)",
      },
      backgroundImage: {
        "indigo-glow":
          "radial-gradient(80% 60% at 50% 0%, hsl(var(--primary) / 0.18), transparent 60%)",
        "violet-glow":
          "radial-gradient(60% 60% at 50% 50%, hsl(var(--accent) / 0.22), transparent 70%)",
        "grid-faint":
          "linear-gradient(hsl(var(--border)) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--border)) 1px, transparent 1px)",
      },
      boxShadow: {
        // Premium card shadow — pairs with dark cards over near-black bg.
        soft: "0 1px 0 0 hsl(var(--border)) inset, 0 8px 32px -16px rgb(0 0 0 / 0.6)",
        glow: "0 0 0 1px hsl(var(--primary) / 0.6), 0 8px 40px -8px hsl(var(--primary) / 0.45)",
        "inner-glow":
          "inset 0 1px 0 0 hsl(var(--primary) / 0.12), 0 0 32px -8px hsl(var(--primary) / 0.35)",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        "pulse-soft": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
        "ring-pulse": {
          "0%": { boxShadow: "0 0 0 0 hsl(var(--primary) / 0.45)" },
          "70%": { boxShadow: "0 0 0 10px hsl(var(--primary) / 0)" },
          "100%": { boxShadow: "0 0 0 0 hsl(var(--primary) / 0)" },
        },
        "gradient-shift": {
          "0%, 100%": { backgroundPosition: "0% 50%" },
          "50%": { backgroundPosition: "100% 50%" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "pulse-soft": "pulse-soft 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "ring-pulse": "ring-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "gradient-shift": "gradient-shift 6s ease infinite",
        "fade-up": "fade-up 0.4s ease-out both",
        shimmer: "shimmer 2.4s linear infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
