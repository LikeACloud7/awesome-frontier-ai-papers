import type { Metadata } from "next";
import "./globals.css";

const siteUrl = "https://likeacloud7.github.io/awesome-frontier-ai-papers";
const description =
  "An Awesome-style GitHub repository of frontier AI lab papers, with a clean web view for searching and filtering the generated paper index.";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "Awesome Frontier AI Papers",
    template: "%s | Awesome Frontier AI Papers"
  },
  description,
  keywords: [
    "awesome frontier ai papers",
    "frontier AI papers",
    "AI research papers",
    "LLM papers",
    "OpenAI papers",
    "Anthropic papers",
    "Google DeepMind papers",
    "Meta FAIR papers",
    "Qwen papers",
    "DeepSeek papers",
    "AI technical reports",
    "model cards",
    "system cards",
    "AI lab research tracker"
  ],
  alternates: {
    canonical: "/"
  },
  openGraph: {
    title: "Awesome Frontier AI Papers",
    description,
    url: siteUrl,
    siteName: "Awesome Frontier AI Papers",
    type: "website"
  },
  twitter: {
    card: "summary_large_image",
    title: "Awesome Frontier AI Papers",
    description
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true
    }
  },
  applicationName: "Awesome Frontier AI Papers",
  authors: [{ name: "LikeACloud7", url: "https://github.com/LikeACloud7" }],
  creator: "LikeACloud7",
  publisher: "LikeACloud7"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
