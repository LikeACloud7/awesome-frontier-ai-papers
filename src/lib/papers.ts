export type Company = {
  id: string;
  name: string;
  group_id: string;
  group_name: string;
  region: string;
  aliases: string[];
  paper_count: number;
  latest_paper_date: string;
};

export type Paper = {
  id: string;
  title: string;
  url: string;
  published: string;
  authors: string[];
  abstract: string;
  companies: string[];
  matched_orgs?: string[];
  company_groups: string[];
  company_regions: string[];
  sources: string[];
  source: string;
  work_type: string;
  doi: string;
  openalex_id: string;
  cited_by_count: number;
  quality_score: number;
  matched_keywords: string[];
  author_affiliations: string[];
  concepts: {
    id: string;
    display_name: string;
    score: number;
  }[];
};

export type PaperDataset = {
  generated_at: string;
  collection?: {
    status: "ok" | "partial";
    failed_sources: number;
    error_sources?: number;
    partial_sources: number;
    pending_metadata: number;
  };
  source_notes: string[];
  totals: {
    papers: number;
    companies: number;
    tracked_companies: number;
  };
  companies: Company[];
  papers: Paper[];
};

export const regionLabels: Record<string, string> = {
  all: "All",
  US: "US",
  China: "China"
};

export function formatDate(value: string): string {
  if (!value) return "No date";
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(date);
}

export function formatGeneratedAt(value: string): string {
  if (!value) return "Not generated";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

export function companyNames(paper: Paper): string[] {
  return paper.companies?.length ? paper.companies : paper.matched_orgs || [];
}

export function safePaperUrl(value: string): string | undefined {
  if (!/^https?:\/\//i.test(value) || /[\u0000-\u0020\u007f\\]/.test(value)) return undefined;
  try {
    if (/[\u0000-\u001f\u007f\\]/.test(decodeURIComponent(value))) return undefined;
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    if (url.username || url.password || (url.port && !["80", "443"].includes(url.port))) return undefined;
    if (!host.includes(".") || host.startsWith("[") || /(?:^|\.)(?:localhost|local|internal)$/.test(host)) return undefined;
    if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
      const [a, b, c] = host.split(".").map(Number);
      if (a === 0 || a === 10 || a === 127 || a >= 224 ||
          (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254) ||
          (a === 172 && b >= 16 && b <= 31) || (a === 192 && (b === 168 || b === 0)) ||
          (a === 198 && (b === 18 || b === 19 || (b === 51 && c === 100))) ||
          (a === 203 && b === 0 && c === 113)) return undefined;
    }
    return url.href;
  } catch {
    return undefined;
  }
}

export function paperMatchesQuery(paper: Paper, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;

  const text = [
    paper.title,
    paper.abstract,
    companyNames(paper).join(" "),
    paper.authors.join(" "),
    paper.matched_keywords.join(" ")
  ].join(" ").toLowerCase();

  return text.includes(normalized);
}
