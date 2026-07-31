/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // A static export when BHOOMI_STATIC_EXPORT is set, a server build otherwise.
  //
  // Conditional rather than always-on because the two deployments want
  // different things. Docker runs `next start`, which needs the server build.
  // Render's free tier, though, sleeps a web service after 15 minutes but
  // serves static sites free and always-on -- and this frontend is entirely
  // client-side (it talks to the API from the browser), so there is nothing
  // for a server to do. Exporting it is what keeps the UI up while the API is
  // asleep, which is the difference between a demo that looks broken and one
  // that shows a spinner for 40 seconds.
  ...(process.env.BHOOMI_STATIC_EXPORT ? { output: "export" } : {}),
  // The API runs separately -- it needs a fixed IP for Bhoonidhi (PLAN.md 2.2),
  // so it cannot be a Next.js route handler on serverless.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  },
};
export default nextConfig;
