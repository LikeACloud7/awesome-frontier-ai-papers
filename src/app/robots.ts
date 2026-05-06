import type { MetadataRoute } from "next";

export const dynamic = "force-static";

const siteUrl = "https://likeacloud7.github.io/awesome-frontier-ai-papers";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/"
    },
    sitemap: `${siteUrl}/sitemap.xml`
  };
}
