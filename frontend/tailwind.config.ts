import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: "#172026",
        mint: "#7ad6c2",
        ember: "#f78b5d",
        sand: "#fff8ed",
      },
      boxShadow: {
        float: "0 14px 40px rgba(23, 32, 38, 0.15)",
      },
      borderRadius: {
        xl2: "1.25rem",
      },
      keyframes: {
        reveal: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        reveal: "reveal 0.35s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
