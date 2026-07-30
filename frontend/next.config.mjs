/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The API runs separately -- it needs a fixed IP for Bhoonidhi (PLAN.md 2.2),
  // so it cannot be a Next.js route handler on serverless.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  },
};
export default nextConfig;
