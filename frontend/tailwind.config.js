/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#09090b",
        card: "#111113",
        border: "#27272a",
        primary: "#22c55e",
      },
      boxShadow: {
        glow: "0 0 30px rgba(34,197,94,.12)",
      },
    },
  },
  plugins: [],
};
