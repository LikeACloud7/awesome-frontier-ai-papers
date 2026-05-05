import type { MetadataRoute } from "next";

export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: "https://likeacloud7.github.io/",
      lastModified: new Date(),
      changeFrequency: "daily",
      priority: 1
    },
    {
      url: "https://likeacloud7.github.io/data/company_papers.json",
      lastModified: new Date(),
      changeFrequency: "daily",
      priority: 0.8
    },
    {
      url: "https://likeacloud7.github.io/llms.txt",
      lastModified: new Date(),
      changeFrequency: "weekly",
      priority: 0.7
    }
  ];
}
