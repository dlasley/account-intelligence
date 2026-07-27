/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    // Next's defaults cover the individual @radix-ui/react-* packages but not the
    // unified `radix-ui` barrel the UI primitives import from.
    optimizePackageImports: ['radix-ui'],
  },
};
export default nextConfig;
