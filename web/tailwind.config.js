/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#f6f2ea",
        card: "#fffdf8",
        ink: "#1f1b16",
        muted: "#6f675c",
        line: "#e2dacd",
        shu: "#c0432a", // 朱色（アクセント）
        "shu-soft": "#e7d3cb",
        moss: "#5c6b4a",
        sky: "#3f6b78",
      },
      fontFamily: {
        display: ['"Shippori Mincho"', "serif"],
        sans: ['"Zen Kaku Gothic New"', "system-ui", "sans-serif"],
        mono: ['"Space Mono"', "ui-monospace", "monospace"],
      },
      boxShadow: {
        soft: "0 1px 2px rgba(31,27,22,0.04), 0 8px 24px -12px rgba(31,27,22,0.18)",
      },
    },
  },
  plugins: [],
};
