/** @type {import('next').NextConfig} */
const nextConfig = {
  devIndicators: false,
  // Proxy /chat requests to the backend server
  async rewrites() {
    return [
      {
        source: "/chat",
        destination: "http://127.0.0.1:8000/chat",
      },
      {
        source: "/chatkit",
        destination: "http://127.0.0.1:8000/chatkit",
      },
      {
        source: "/chatkit/:path*",
        destination: "http://127.0.0.1:8000/chatkit/:path*",
      },
      {
        source: "/attachments/:path*",
        destination: "http://127.0.0.1:8000/attachments/:path*",
      },
      {
        source: "/traces",
        destination: "http://127.0.0.1:8000/traces",
      },
      {
        source: "/approvals",
        destination: "http://127.0.0.1:8000/approvals",
      },
      {
        source: "/approvals/:path*",
        destination: "http://127.0.0.1:8000/approvals/:path*",
      },
      {
        source: "/stt",
        destination: "http://127.0.0.1:8000/stt",
      },
    ];
  },
};

export default nextConfig;
