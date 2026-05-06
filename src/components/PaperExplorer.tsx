"use client";

import {
  ArrowUpRight,
  BarChart3,
  Building2,
  CalendarClock,
  Code2,
  Database,
  FileText,
  Filter,
  Globe2,
  Search,
  Sparkles,
  TimerReset
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  companyNames,
  formatDate,
  formatGeneratedAt,
  paperMatchesQuery,
  regionLabels,
  type Company,
  type Paper,
  type PaperDataset
} from "@/lib/papers";

type RegionFilter = "all" | "US" | "China";

const regionOptions: RegionFilter[] = ["all", "US", "China"];
const datasetUrl = "data/company_papers.json";
const githubUrl = "https://github.com/LikeACloud7/awesome-frontier-ai-papers";
const coverageUrl = `${githubUrl}/blob/main/docs/COVERAGE.md`;
const pageSize = 120;
const emptyDataset: PaperDataset = {
  generated_at: "",
  source_notes: [],
  totals: {
    papers: 0,
    companies: 0,
    tracked_companies: 0
  },
  companies: [],
  papers: []
};

function sourceLabel(source: string): string {
  if (source === "openalex") return "OpenAlex";
  if (source === "official_report" || source === "official_repository_scan") return "Official report";
  if (source === "official_publication_page") return "Official page";
  if (source === "huggingface_search") return "HuggingFace";
  if (source === "arxiv") return "arXiv";
  if (source === "huggingface") return "HuggingFace";
  return source || "source";
}

function getCompanyCount(companies: Company[], region: RegionFilter): number {
  return companies.filter((company) => {
    if (region === "all") return true;
    return company.region === region;
  }).length;
}

function labInitials(name: string): string {
  const clean = name.replace(/Google\/DeepMind/, "DeepMind").replace(/Meta\/FAIR/, "FAIR");
  const parts = clean.split(/[\/\s-]+/).filter(Boolean);
  if (!parts.length) return "AI";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return parts
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function compactNumber(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact" }).format(value);
}

function CompanyRow({
  company,
  selected,
  onSelect
}: {
  company: Company;
  selected: boolean;
  onSelect: (name: string) => void;
}) {
  return (
    <button
      className={`company-row${selected ? " selected" : ""}`}
      onClick={() => onSelect(company.name)}
      type="button"
    >
      <span className="lab-avatar" aria-hidden>
        {labInitials(company.name)}
      </span>
      <span className="company-copy">
        <strong>{company.name}</strong>
        <small>{company.latest_paper_date ? formatDate(company.latest_paper_date) : "No papers yet"}</small>
      </span>
      <em>{compactNumber(company.paper_count)}</em>
    </button>
  );
}

function PaperRow({ paper }: { paper: Paper }) {
  const companies = companyNames(paper);
  const authors = paper.authors.slice(0, 5).join(", ");
  const extraAuthors = paper.authors.length > 5 ? ` +${paper.authors.length - 5}` : "";

  return (
    <article className="paper-row">
      <div className="paper-date">
        <span>
          <CalendarClock size={14} aria-hidden />
          {formatDate(paper.published)}
        </span>
      </div>

      <div className="paper-main">
        <div className="paper-meta">
          {companies.map((company) => (
            <span className="company-chip" key={company}>
              {company}
            </span>
          ))}
          {paper.sources.slice(0, 2).map((source) => (
            <span className="source-chip" key={source}>
              {sourceLabel(source)}
            </span>
          ))}
        </div>

        <h2>
          <a href={paper.url} rel="noreferrer" target="_blank">
            {paper.title}
            <ArrowUpRight size={16} aria-hidden />
          </a>
        </h2>

        {authors ? (
          <p className="authors">
            {authors}
            {extraAuthors}
          </p>
        ) : null}

        {paper.abstract ? <p className="abstract">{paper.abstract}</p> : null}
      </div>

      <div className="paper-signals" aria-label="Paper signals">
        <span>
          <Sparkles size={14} aria-hidden />
          {paper.quality_score}
        </span>
        <span>{paper.cited_by_count} cites</span>
        {paper.matched_keywords.slice(0, 3).map((keyword) => (
          <span key={keyword}>{keyword}</span>
        ))}
      </div>
    </article>
  );
}

export default function PaperExplorer() {
  const [dataset, setDataset] = useState<PaperDataset>(emptyDataset);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [region, setRegion] = useState<RegionFilter>("all");
  const [selectedCompany, setSelectedCompany] = useState("all");
  const [selectedSource, setSelectedSource] = useState("all");
  const [query, setQuery] = useState("");
  const [visibleLimit, setVisibleLimit] = useState(pageSize);

  useEffect(() => {
    let cancelled = false;

    fetch(datasetUrl)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Failed to load dataset: ${response.status}`);
        }
        return response.json() as Promise<PaperDataset>;
      })
      .then((payload) => {
        if (!cancelled) {
          setDataset(payload);
          setIsLoading(false);
        }
      })
      .catch((error: Error) => {
        if (!cancelled) {
          setLoadError(error.message);
          setIsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const companies = useMemo(() => {
    return dataset.companies
      .filter((company) => company.paper_count > 0)
      .filter((company) => region === "all" || company.region === region)
      .sort((a, b) => {
        const dateCompare = b.latest_paper_date.localeCompare(a.latest_paper_date);
        if (dateCompare !== 0) return dateCompare;
        return b.paper_count - a.paper_count;
      });
  }, [dataset.companies, region]);

  const sourceOptions = useMemo(() => {
    const sources = new Set<string>();
    dataset.papers.forEach((paper) => {
      paper.sources.forEach((source) => sources.add(source));
    });
    return Array.from(sources).sort((a, b) => sourceLabel(a).localeCompare(sourceLabel(b)));
  }, [dataset.papers]);

  const visiblePapers = useMemo(() => {
    return dataset.papers
      .filter((paper) => {
        if (region !== "all" && !paper.company_regions.includes(region)) return false;
        if (selectedCompany !== "all" && !companyNames(paper).includes(selectedCompany)) return false;
        if (selectedSource !== "all" && !paper.sources.includes(selectedSource)) return false;
        return paperMatchesQuery(paper, query);
      })
      .sort((a, b) => {
        const dateCompare = b.published.localeCompare(a.published);
        if (dateCompare !== 0) return dateCompare;
        return b.quality_score - a.quality_score;
      });
  }, [dataset.papers, query, region, selectedCompany, selectedSource]);

  useEffect(() => {
    setVisibleLimit(pageSize);
  }, [query, region, selectedCompany, selectedSource]);

  const renderedPapers = visiblePapers.slice(0, visibleLimit);
  const latestDate = dataset.papers[0]?.published || "";
  const selectedCompanyLabel = selectedCompany === "all" ? "All labs" : selectedCompany;
  const activeFilters = [
    region !== "all" ? regionLabels[region] : null,
    selectedCompany !== "all" ? selectedCompany : null,
    selectedSource !== "all" ? sourceLabel(selectedSource) : null,
    query.trim() ? `"${query.trim()}"` : null
  ].filter(Boolean);
  const topCompanies = dataset.companies
    .slice()
    .sort((a, b) => b.paper_count - a.paper_count)
    .slice(0, 6);

  function resetFilters() {
    setRegion("all");
    setSelectedCompany("all");
    setSelectedSource("all");
    setQuery("");
    setVisibleLimit(pageSize);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark" aria-hidden>
            <FileText size={20} />
          </div>
          <div>
            <h1>Awesome Frontier AI Papers</h1>
            <p>{isLoading ? "Loading dataset" : `Updated ${formatGeneratedAt(dataset.generated_at)}`}</p>
          </div>
        </div>

        <nav className="top-links" aria-label="Project links">
          <a href={githubUrl} rel="noreferrer" target="_blank">
            <Code2 size={16} aria-hidden />
            GitHub
          </a>
          <a href={coverageUrl} rel="noreferrer" target="_blank">
            <FileText size={16} aria-hidden />
            Coverage
          </a>
          <a href={datasetUrl}>
            <Database size={16} aria-hidden />
            Dataset
          </a>
        </nav>
      </header>

      <section className="hero-panel" aria-label="Overview">
        <div className="hero-copy">
          <h2>Browse frontier AI papers by lab.</h2>
          <p>
            Search the generated paper index by lab, date, source, author, and keyword.
          </p>
        </div>

        <div className="summary-strip" aria-label="Dataset summary">
          <span>
            <strong>{compactNumber(dataset.totals.papers)}</strong>
            papers
          </span>
          <span>
            <strong>{dataset.totals.tracked_companies}</strong>
            labs
          </span>
          <span>
            <strong>{latestDate ? formatDate(latestDate) : "None"}</strong>
            latest
          </span>
        </div>
      </section>

      <section className="toolbar" aria-label="Filters">
        <div className="search-box">
          <Search size={17} aria-hidden />
          <input
            aria-label="Search papers"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search title, abstract, lab, author, keyword"
            type="search"
            value={query}
          />
        </div>

        <div className="filter-cluster">
          <div className="region-control" aria-label="Region filter">
            {regionOptions.map((option) => (
              <button
                className={region === option ? "active" : ""}
                key={option}
                onClick={() => {
                  setRegion(option);
                  setSelectedCompany("all");
                  setVisibleLimit(pageSize);
                }}
                type="button"
              >
                {regionLabels[option]}
                <span>{getCompanyCount(dataset.companies, option)}</span>
              </button>
            ))}
          </div>

          <label className="source-select">
            <span>Source</span>
            <select
              aria-label="Source filter"
              onChange={(event) => setSelectedSource(event.target.value)}
              value={selectedSource}
            >
              <option value="all">All sources</option>
              {sourceOptions.map((source) => (
                <option key={source} value={source}>
                  {sourceLabel(source)}
                </option>
              ))}
            </select>
          </label>

          {activeFilters.length ? (
            <button className="reset-button" onClick={resetFilters} type="button">
              <TimerReset size={15} aria-hidden />
              Reset
            </button>
          ) : null}
        </div>
      </section>

      <section className="workspace">
        <aside className="company-panel">
          <div className="panel-heading">
            <span>
              <Building2 size={16} aria-hidden />
              Labs
            </span>
            <small>{companies.length}</small>
          </div>

          <button
            className={`company-row all${selectedCompany === "all" ? " selected" : ""}`}
            onClick={() => setSelectedCompany("all")}
            type="button"
          >
            <span className="lab-avatar" aria-hidden>
              AI
            </span>
            <span className="company-copy">
              <strong>All labs</strong>
              <small>{region === "all" ? "US + China" : regionLabels[region]}</small>
            </span>
            <em>{compactNumber(visiblePapers.length)}</em>
          </button>

          <div className="company-list">
            {companies.map((company) => (
              <CompanyRow
                company={company}
                key={company.name}
                onSelect={setSelectedCompany}
                selected={selectedCompany === company.name}
              />
            ))}
          </div>
        </aside>

        <section className="paper-panel">
          <div className="paper-panel-header">
            <div>
              <p className="section-label">
                <Filter size={14} aria-hidden />
                {selectedCompanyLabel}
              </p>
              <h2>{compactNumber(visiblePapers.length)} papers</h2>
            </div>
            <div className="active-filter-row" aria-label="Active filters">
              {activeFilters.length ? activeFilters.map((filter) => <span key={filter}>{filter}</span>) : <span>Latest first</span>}
            </div>
          </div>

          <div className="paper-list">
            {loadError ? (
              <div className="empty-state">
                <FileText size={24} aria-hidden />
                <p>{loadError}</p>
              </div>
            ) : isLoading ? (
              <div className="empty-state">
                <FileText size={24} aria-hidden />
                <p>Loading company paper archive...</p>
              </div>
            ) : visiblePapers.length ? (
              <>
                {renderedPapers.map((paper) => <PaperRow key={paper.id} paper={paper} />)}
                {renderedPapers.length < visiblePapers.length ? (
                  <button
                    className="load-more"
                    onClick={() => setVisibleLimit((current) => current + pageSize)}
                    type="button"
                  >
                    Load more
                    <span>
                      {renderedPapers.length} / {visiblePapers.length}
                    </span>
                  </button>
                ) : null}
              </>
            ) : (
              <div className="empty-state">
                <FileText size={24} aria-hidden />
                <p>No papers match the current filters.</p>
              </div>
            )}
          </div>
        </section>

        <aside className="insight-panel" aria-label="Dataset insight">
          <section>
            <div className="panel-heading compact">
              <span>
                <BarChart3 size={16} aria-hidden />
                Top labs
              </span>
            </div>
            <div className="rank-list">
              {topCompanies.map((company, index) => (
                <button key={company.name} onClick={() => setSelectedCompany(company.name)} type="button">
                  <span>{index + 1}</span>
                  <strong>{company.name}</strong>
                  <em>{compactNumber(company.paper_count)}</em>
                </button>
              ))}
            </div>
          </section>

          <section className="policy-card">
            <div className="panel-heading compact">
              <span>
                <Globe2 size={16} aria-hidden />
                Sources
              </span>
            </div>
            <p>
              Official publication pages, model cards, system cards, company repositories, HuggingFace Papers, and
              OpenAlex affiliation metadata.
            </p>
            <a href={coverageUrl} rel="noreferrer" target="_blank">
              Coverage details
              <ArrowUpRight size={15} aria-hidden />
            </a>
          </section>
        </aside>
      </section>
    </main>
  );
}
