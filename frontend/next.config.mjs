/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Lets a second dev server (for example one pointed at a test backend) run beside the usual one without sharing its build folder.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Docker builds set NEXT_OUTPUT=standalone; local `npm start` uses the normal output.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  poweredByHeader: false,
  // The floating "N" dev badge sits on top of the page corner; the dev overlay still shows real errors.
  devIndicators: false,
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "X-Frame-Options", value: "DENY" },
      ],
    }];
  },
};
export default nextConfig;
