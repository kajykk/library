import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "个人知识库",
    short_name: "知识库",
    description: "自托管、本地优先的个人知识库",
    start_url: "/library",
    display: "standalone",
    background_color: "#f8fafc",
    theme_color: "#1e293b",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
