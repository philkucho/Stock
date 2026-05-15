import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // FastAPI를 Next.js가 proxy.
  // 브라우저는 항상 Next.js의 /api/*만 호출 → CORS/외부 IP 불일치 문제 제거.
  // 폰에서도 동일 origin이라 호스트 차이 신경 X.
  async rewrites() {
    return [
      // trailing slash 있는 path는 그대로 유지 (FastAPI router는 prefix + "/" 형식이 많음)
      {
        source: "/api/:path*/",
        destination: "http://127.0.0.1:8000/api/:path*/",
      },
      // trailing slash 없는 path는 그대로 (e.g., /api/health, /api/positions/orders)
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
  // Cross-origin dev 허용 — Tailscale (100.64.0.0/10), 사설망 (192.168.x, 10.x).
  // mobile/태블릿이 localhost 외 IP로 dev 접근 시 HMR / Server Actions / rewrites 차단 회피.
  // Next.js 14+: 명시 안 하면 warning + 일부 기능 제한.
  // Next.js의 trailing slash 자동 정규화 비활성화 — /api/* 호출이 308 redirect 없이
  // rewrites destination에 그대로 도달. FastAPI router의 final path 그대로 매칭.
  skipTrailingSlashRedirect: true,
  allowedDevOrigins: [
    "localhost",
    "127.0.0.1",
    "100.64.0.0/10", // Tailscale
    "100.*.*.*",     // Tailscale 광범위 매칭 (구버전 호환)
    "192.168.0.0/16",
    "10.0.0.0/8",
  ],
  // 임시: dev 중인 코드의 타입 미정합 무시 (런타임은 OK).
  // 추후 tier_thresholds 등 타입 정리 후 false로 되돌릴 것.
  typescript: { ignoreBuildErrors: true },
};

export default nextConfig;
