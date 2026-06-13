import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      fontFamily: {
        // Wired to the next/font CSS vars set in layout.tsx.
        heading: ["var(--font-heading)", "ui-sans-serif", "system-ui", "sans-serif"],
        body: ["var(--font-body)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      colors: {
        ink: "#172026",
        mint: "#7ad6c2",
        ember: "#f78b5d",
        sand: "#fff8ed",
        // Semantic surface tokens — warm whites that sit on the gradient
        // page without the dead flatness of pure #fff.
        surface: "#fffdf9",
        cream: "#fff3e2",
      },
      boxShadow: {
        // A real elevation ramp instead of one flat drop shadow. All three
        // are warm-tinted (ink-based) and layered (contact + ambient) so
        // cards read as lifted paper rather than CSS boxes.
        soft: "0 1px 2px rgba(23, 32, 38, 0.04), 0 6px 16px -10px rgba(23, 32, 38, 0.14)",
        float: "0 2px 6px rgba(23, 32, 38, 0.05), 0 18px 40px -20px rgba(23, 32, 38, 0.24)",
        lift: "0 6px 14px rgba(23, 32, 38, 0.08), 0 30px 60px -26px rgba(23, 32, 38, 0.30)",
        glow: "0 0 0 1px rgba(122, 214, 194, 0.45), 0 12px 32px -12px rgba(122, 214, 194, 0.40)",
      },
      borderRadius: {
        xl2: "1.25rem",
        "4xl": "2rem",
      },
      keyframes: {
        reveal: {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "float-in": {
          "0%": { opacity: "0", transform: "translateY(16px) scale(0.985)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        reveal: "reveal 0.42s cubic-bezier(0.22, 1, 0.36, 1)",
        "float-in": "float-in 0.5s cubic-bezier(0.22, 1, 0.36, 1)",
        shimmer: "shimmer 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
