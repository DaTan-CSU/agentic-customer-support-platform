/** @type {import('next').NextConfig} */
// Compose supplies the service name; local development uses the host port.
const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const nextConfig = {
  devIndicators: false,
  // Proxy /chat requests to the backend server
  async rewrites() {
    return [
      {
        source: "/chat",
        destination: `${backendUrl}/chat`,
      },
      {
        source: "/chatkit",
        destination: `${backendUrl}/chatkit`,
      },
      {
        source: "/chatkit/:path*",
        destination: `${backendUrl}/chatkit/:path*`,
      },
      {
        source: "/attachments/:path*",
        destination: `${backendUrl}/attachments/:path*`,
      },
      {
        source: "/traces",
        destination: `${backendUrl}/traces`,
      },
      {
        source: "/approvals",
        destination: `${backendUrl}/approvals`,
      },
      {
        source: "/approvals/:path*",
        destination: `${backendUrl}/approvals/:path*`,
      },
      {
        source: "/stt",
        destination: `${backendUrl}/stt`,
      },
    ];
  },
};

export default nextConfig;
