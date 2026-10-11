/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Lets a second dev server (for example one pointed at a test backend) run beside the usual one without sharing its build folder.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Docker builds set NEXT_OUTPUT=standalone; local `npm start` uses the normal output.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  poweredByHeader: false,
  // Page metadata (title, link-preview tags) goes in <head> for every visitor, not only for known bots. Next otherwise
  // streams it into the body for browsers, and WhatsApp Desktop, which fetches previews like a browser and reads only
  // <head>, then shows a bare link instead of the trip card. Only the share page has slow metadata, so this costs little.
  htmlLimitedBots: /.*/,
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
