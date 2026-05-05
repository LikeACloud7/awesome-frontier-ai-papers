const repoName = process.env.GITHUB_REPOSITORY?.split("/")[1] || "awesome-frontier-ai-papers";
const isUserPagesRepo = repoName.toLowerCase().endsWith(".github.io");
const githubPagesBasePath = process.env.GITHUB_ACTIONS === "true" && !isUserPagesRepo ? `/${repoName}` : "";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || githubPagesBasePath;

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  basePath,
  assetPrefix: basePath ? `${basePath}/` : undefined,
  turbopack: {
    root: process.cwd()
  },
  images: {
    unoptimized: true
  }
};

export default nextConfig;
