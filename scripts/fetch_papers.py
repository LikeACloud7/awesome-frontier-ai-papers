#!/usr/bin/env python3
"""
arXiv 논문 수집 스크립트
최근 N일간의 논문을 수집하고 키워드/기관 기반으로 필터링
HTML UI 생성 및 브라우저 오픈
이미 본 논문은 중복 제외
"""

import argparse
import json
import re
import sys
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import feedparser
import requests

# 출력 디렉토리
OUTPUT_DIR = Path(__file__).parent.parent / "output"
SEEN_FILE = OUTPUT_DIR / "seen.json"


def load_seen_papers() -> dict:
    """이미 본 논문 ID 로드"""
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"papers": {}}  # {arxiv_id: "2025-01-29"}


def save_seen_papers(seen: dict):
    """본 논문 ID 저장"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, ensure_ascii=False, indent=2, fp=f)


def cleanup_old_seen(seen: dict, days: int = 14) -> dict:
    """오래된 기록 정리 (기본 14일)"""
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    seen["papers"] = {
        k: v for k, v in seen["papers"].items()
        if v >= cutoff_str
    }
    return seen


def get_arxiv_id(url: str) -> str:
    """URL에서 arXiv ID 추출"""
    return url.split("/abs/")[-1].split("v")[0]  # 버전 제거


def load_config():
    """설정 파일 로드"""
    config_path = Path(__file__).parent.parent / "config" / "interests.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_huggingface_daily_papers(max_results: int = 10) -> list[dict]:
    """HuggingFace Daily Papers에서 인기 논문 가져오기"""
    url = "https://huggingface.co/api/daily_papers"

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"HuggingFace API 요청 실패: {str(e)}", file=sys.stderr)
        return []

    papers = []
    for item in data[:max_results]:
        paper_data = item.get("paper", {})
        arxiv_id = paper_data.get("id", "")

        paper = {
            "title": paper_data.get("title", "").replace("\n", " ").strip(),
            "authors": [a.get("name", "") for a in paper_data.get("authors", [])],
            "abstract": paper_data.get("summary", "").replace("\n", " ").strip(),
            "categories": [],
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "published": paper_data.get("publishedAt", "")[:10],
            "affiliations": [],
            "matched_keywords": [],
            "matched_orgs": [],
            "upvotes": item.get("paper", {}).get("upvotes", 0),
        }
        papers.append(paper)

    return papers


def fetch_arxiv_papers(categories: list[str], days_back: int = 2, max_results: int = 500):
    """arXiv API에서 논문 수집"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)

    cat_query = " OR ".join([f"cat:{cat}" for cat in categories])
    query = f"({cat_query})"

    base_url = "http://export.arxiv.org/api/query?"
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending"
    }

    url = base_url + urlencode(params)

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"arXiv API 요청 실패: {str(e)}", file=sys.stderr)
        sys.exit(1)

    feed = feedparser.parse(response.content)

    papers = []
    for entry in feed.entries:
        published = entry.get("published", "")
        if published:
            try:
                pub_date = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ")
                if pub_date < start_date:
                    continue
            except ValueError:
                pass

        categories_list = [tag.get("term", "") for tag in entry.get("tags", [])]
        arxiv_id = entry.get("id", "").split("/abs/")[-1]

        paper = {
            "title": entry.get("title", "").replace("\n", " ").strip(),
            "authors": [author.get("name", "") for author in entry.get("authors", [])],
            "abstract": entry.get("summary", "").replace("\n", " ").strip(),
            "categories": categories_list,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "published": published[:10] if published else "",
            "affiliations": [],
            "matched_keywords": [],
            "matched_orgs": []
        }
        papers.append(paper)

    return papers


def extract_affiliations(authors: list[str], abstract: str, title: str):
    """저자 정보와 텍스트에서 기관 추출"""
    affiliations = set()
    text = " ".join(authors) + " " + abstract + " " + title

    org_patterns = {
        "Google": [r"\bGoogle\b", r"\bDeepMind\b"],
        "DeepMind": [r"\bDeepMind\b"],
        "Meta": [r"\bMeta\b", r"\bFAIR\b", r"Facebook AI"],
        "Microsoft": [r"\bMicrosoft\b"],
        "OpenAI": [r"\bOpenAI\b"],
        "Anthropic": [r"\bAnthropic\b"],
        "Amazon": [r"\bAmazon\b", r"\bAWS\b"],
        "Apple": [r"\bApple\b"],
        "DeepSeek": [r"\bDeepSeek\b"],
        "Moonshot": [r"\bMoonshot\b", r"\bKimi\b"],
        "Alibaba": [r"\bAlibaba\b", r"\bDAMO\b"],
        "Baidu": [r"\bBaidu\b"],
        "Tencent": [r"\bTencent\b"]
    }

    for org, patterns in org_patterns.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                affiliations.add(org)
                break

    return list(affiliations)


def match_keywords(text: str, keywords: list[str]):
    """텍스트에서 키워드 매칭"""
    matched = []
    text_lower = text.lower()

    for keyword in keywords:
        pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
        if re.search(pattern, text_lower):
            matched.append(keyword)

    return matched


def filter_papers(papers: list[dict], config: dict):
    """키워드와 기관 기반으로 논문 필터링"""
    keywords = config.get("keywords", [])
    organizations = config.get("organizations", [])

    filtered = []

    for paper in papers:
        text = paper["title"] + " " + paper["abstract"]
        matched_keywords = match_keywords(text, keywords)
        affiliations = extract_affiliations(paper["authors"], paper["abstract"], paper["title"])
        matched_orgs = [org for org in affiliations if org in organizations]

        if matched_keywords or matched_orgs:
            paper["matched_keywords"] = matched_keywords
            paper["matched_orgs"] = matched_orgs
            paper["affiliations"] = affiliations
            filtered.append(paper)

    return filtered


def get_keyword_emoji(keywords: list[str]) -> str:
    """키워드에 따른 이모지 반환"""
    emoji_map = {
        "political": "🗳️", "politics": "🗳️", "election": "🗳️", "democracy": "🗳️",
        "personalized": "🧠", "personalization": "🧠", "preference": "🧠",
        "memory": "🧠", "long-term": "🧠",
        "efficient": "⚡", "compression": "⚡", "quantization": "⚡", "distillation": "⚡",
        "agent": "🤖", "multi-agent": "🤖",
        "journalism": "📰", "news": "📰", "media": "📰",
        "persuasion": "💬", "persuasive": "💬",
        "retrieval": "🔍",
    }

    emojis = set()
    for kw in keywords:
        if kw.lower() in emoji_map:
            emojis.add(emoji_map[kw.lower()])

    return " ".join(emojis) if emojis else "📚"


# 카테고리 정의
CATEGORIES = {
    "hot": {
        "name": "🔥 Hot Papers",
        "description": "HuggingFace Daily Papers 인기 논문",
        "source": "huggingface",
    },
    "bigtech": {
        "name": "🏢 Big-tech",
        "description": "주요 기업 연구",
        "check": lambda p: len(p.get("matched_orgs", [])) > 0,
    },
    "domain": {
        "name": "🎯 Domain",
        "description": "설득, 정치, 저널리즘",
        "keywords": ["persuasion", "persuasive", "political", "politics", "election", "democracy", "journalism", "news", "media"],
    },
    "personalized": {
        "name": "🧠 Personalization & Memory",
        "description": "개인화, 장기 기억, 선호도",
        "keywords": ["personalized", "personalization", "preference", "memory", "long-term"],
    },
    "efficient": {
        "name": "⚡ Efficient LLM",
        "description": "효율화, 압축, 양자화",
        "keywords": ["efficient", "compression", "quantization", "distillation"],
    },
}

CATEGORY_ORDER = ["hot", "bigtech", "domain", "personalized", "efficient"]


def categorize_paper(paper: dict) -> str:
    """논문을 카테고리에 배정 (우선순위 기반, 하나만)"""
    keywords = [kw.lower() for kw in paper.get("matched_keywords", [])]

    # hot, other 제외하고 분류
    for cat_id in CATEGORY_ORDER:
        if cat_id == "hot":
            continue
        cat = CATEGORIES[cat_id]
        if "check" in cat:
            if cat["check"](paper):
                return cat_id
        elif "keywords" in cat:
            if any(kw in keywords for kw in cat["keywords"]):
                return cat_id

    return None  # 해당 없음


def categorize_papers(papers: list[dict], max_per_category: int = 10) -> dict:
    """논문을 카테고리별로 분류"""
    categorized = {cat_id: [] for cat_id in CATEGORY_ORDER}

    for paper in papers:
        cat_id = categorize_paper(paper)
        if cat_id and len(categorized[cat_id]) < max_per_category:
            categorized[cat_id].append(paper)

    return categorized


def generate_html(result: dict) -> str:
    """HTML 페이지 생성 (탭 UI)"""
    categorized = result["categorized"]
    date = result["date"]
    total = result["total_found"]
    displayed = result["displayed"]
    skipped = result.get("skipped_seen", 0)

    skipped_text = f" | 이미 본 논문 {skipped}편 제외" if skipped > 0 else ""

    # 논문이 있는 카테고리만 필터링
    active_categories = [(cat_id, CATEGORIES[cat_id], categorized.get(cat_id, []))
                         for cat_id in CATEGORY_ORDER
                         if categorized.get(cat_id, [])]

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>오늘의 AI 논문 - {date}</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        h1 {{ color: #333; border-bottom: 2px solid #4a90d9; padding-bottom: 10px; }}
        .stats {{ color: #666; margin-bottom: 20px; }}
        .controls {{
            background: #4a90d9;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }}
        .controls button {{
            padding: 10px 20px;
            font-size: 14px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            background: white;
            color: #333;
            font-weight: 500;
        }}
        .controls button:hover {{ background: #e8e8e8; }}
        .controls .count {{ color: white; font-weight: bold; margin-left: auto; }}
        .tabs {{
            display: flex;
            gap: 5px;
            margin-bottom: 0;
            flex-wrap: wrap;
        }}
        .tab {{
            padding: 12px 20px;
            background: #ddd;
            border: none;
            border-radius: 8px 8px 0 0;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            color: #666;
            transition: all 0.2s;
        }}
        .tab:hover {{ background: #ccc; }}
        .tab.active {{
            background: white;
            color: #333;
            font-weight: 600;
        }}
        .tab .badge {{
            background: #4a90d9;
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 12px;
            margin-left: 5px;
        }}
        .tab.active .badge {{ background: #333; }}
        .tab-content {{
            display: none;
            background: white;
            border-radius: 0 8px 8px 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .tab-content.active {{ display: block; }}
        .paper {{
            padding: 15px 20px;
            border-bottom: 1px solid #eee;
        }}
        .paper:last-child {{ border-bottom: none; }}
        .paper.selected {{ border-left: 4px solid #4a90d9; background: #f0f7ff; }}
        .paper-header {{
            display: flex;
            align-items: flex-start;
            gap: 10px;
        }}
        .paper-checkbox {{
            width: 20px;
            height: 20px;
            margin-top: 3px;
            cursor: pointer;
        }}
        .paper-num {{
            background: #eee;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            color: #666;
            min-width: 30px;
            text-align: center;
        }}
        .paper-title {{
            flex: 1;
            font-size: 15px;
            font-weight: 600;
            color: #333;
            line-height: 1.4;
        }}
        .paper-title a {{ color: inherit; text-decoration: none; }}
        .paper-title a:hover {{ color: #4a90d9; }}
        .org-tag {{
            background: #e3f2fd;
            color: #1976d2;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }}
        .tags {{
            margin-top: 8px;
            margin-left: 30px;
            display: flex;
            gap: 5px;
            flex-wrap: wrap;
        }}
        .tag {{
            background: #f0f0f0;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            color: #666;
        }}
        .abstract {{
            margin-top: 10px;
            margin-left: 30px;
            padding: 10px;
            background: #fafafa;
            border-radius: 4px;
            font-size: 14px;
            color: #555;
            line-height: 1.6;
            display: none;
        }}
        .abstract.show {{ display: block; }}
        .toggle-abstract {{
            margin-top: 8px;
            margin-left: 30px;
            font-size: 12px;
            color: #4a90d9;
            cursor: pointer;
            border: none;
            background: none;
            padding: 0;
        }}
        .toggle-abstract:hover {{ text-decoration: underline; }}
        .toast {{
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: #333;
            color: white;
            padding: 15px 25px;
            border-radius: 8px;
            display: none;
            z-index: 1000;
        }}
        .toast.show {{ display: block; animation: fadeIn 0.3s; }}
        @keyframes fadeIn {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
    </style>
</head>
<body>
    <h1>오늘의 AI 논문</h1>
    <p class="stats">{date} | 새 논문 {total}편 중 {displayed}편 표시{skipped_text}</p>

    <div class="controls">
        <button onclick="copyNumbers()">📋 선택한 번호 복사</button>
        <button onclick="copyLinks()">🔗 선택한 링크 복사</button>
        <button onclick="selectAllInTab()">✅ 현재 탭 전체 선택</button>
        <button onclick="deselectAll()">❌ 선택 해제</button>
        <span class="count">선택: <span id="selectedCount">0</span>개</span>
    </div>

    <div class="tabs">
"""

    # 탭 버튼 생성
    for i, (cat_id, cat, papers) in enumerate(active_categories):
        active_class = "active" if i == 0 else ""
        html += f"""        <button class="tab {active_class}" onclick="showTab('{cat_id}')" data-tab="{cat_id}">
            {cat['name']} <span class="badge">{len(papers)}</span>
        </button>
"""

    html += """    </div>
"""

    # 전체 논문 번호 매기기용
    paper_num = 1
    all_papers = []

    # 탭 컨텐츠 생성
    for i, (cat_id, cat, papers) in enumerate(active_categories):
        active_class = "active" if i == 0 else ""
        html += f"""
    <div class="tab-content {active_class}" id="tab-{cat_id}">
"""

        for paper in papers:
            orgs = paper.get("matched_orgs", [])
            keywords = paper.get("matched_keywords", [])
            org_tags = "".join([f'<span class="org-tag">{org}</span>' for org in orgs])
            keyword_tags = "".join([f'<span class="tag">{kw}</span>' for kw in keywords])

            abstract_short = paper["abstract"][:300] + "..." if len(paper["abstract"]) > 300 else paper["abstract"]

            html += f"""        <div class="paper" data-num="{paper_num}" data-url="{paper['url']}">
            <div class="paper-header">
                <input type="checkbox" class="paper-checkbox" onchange="updateCount()">
                <span class="paper-num">{paper_num}</span>
                {org_tags}
                <span class="paper-title">
                    <a href="{paper['url']}" target="_blank">{paper['title']}</a>
                </span>
            </div>
            <div class="tags">
                {keyword_tags}
            </div>
            <button class="toggle-abstract" onclick="toggleAbstract(this)">▼ 초록 보기</button>
            <div class="abstract">{abstract_short}</div>
        </div>
"""
            all_papers.append(paper)
            paper_num += 1

        html += """    </div>
"""

    html += """
    <div id="toast" class="toast"></div>

    <script>
        function showTab(tabId) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab[data-tab="${tabId}"]`).classList.add('active');
            document.getElementById(`tab-${tabId}`).classList.add('active');
        }

        function updateCount() {
            const count = document.querySelectorAll('.paper-checkbox:checked').length;
            document.getElementById('selectedCount').textContent = count;

            document.querySelectorAll('.paper').forEach(p => {
                const cb = p.querySelector('.paper-checkbox');
                p.classList.toggle('selected', cb.checked);
            });
        }

        function getSelectedPapers() {
            const selected = [];
            document.querySelectorAll('.paper').forEach(p => {
                if (p.querySelector('.paper-checkbox').checked) {
                    selected.push({
                        num: p.dataset.num,
                        url: p.dataset.url
                    });
                }
            });
            return selected;
        }

        function copyNumbers() {
            const selected = getSelectedPapers();
            if (selected.length === 0) {
                showToast('먼저 논문을 선택하세요');
                return;
            }
            const numbers = selected.map(p => p.num).join(', ');
            const text = numbers + ' 요약해줘';
            navigator.clipboard.writeText(text);
            showToast('복사됨: ' + text);
        }

        function copyLinks() {
            const selected = getSelectedPapers();
            if (selected.length === 0) {
                showToast('먼저 논문을 선택하세요');
                return;
            }
            const links = selected.map(p => p.url).join('\\n');
            navigator.clipboard.writeText(links);
            showToast(selected.length + '개 링크 복사됨');
        }

        function selectAllInTab() {
            const activeTab = document.querySelector('.tab-content.active');
            activeTab.querySelectorAll('.paper-checkbox').forEach(cb => cb.checked = true);
            updateCount();
        }

        function deselectAll() {
            document.querySelectorAll('.paper-checkbox').forEach(cb => cb.checked = false);
            updateCount();
        }

        function toggleAbstract(btn) {
            const abstract = btn.nextElementSibling;
            abstract.classList.toggle('show');
            btn.textContent = abstract.classList.contains('show') ? '▲ 초록 접기' : '▼ 초록 보기';
        }

        function showToast(msg) {
            const toast = document.getElementById('toast');
            toast.textContent = msg;
            toast.classList.add('show');
            setTimeout(() => toast.classList.remove('show'), 2000);
        }
    </script>
</body>
</html>
"""
    return html, all_papers


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(description="arXiv 논문 수집")
    parser.add_argument("--days", type=int, help="수집 기간 (일)")
    args = parser.parse_args()

    config = load_config()

    # 이미 본 논문 로드 및 정리
    seen = load_seen_papers()
    seen = cleanup_old_seen(seen)
    seen_ids = set(seen["papers"].keys())

    # HuggingFace Daily Papers 가져오기 (Hot)
    print("HuggingFace Daily Papers 수집 중...", file=sys.stderr)
    max_per_category = config.get("max_per_category", 10)
    hot_papers = fetch_huggingface_daily_papers(max_results=30)
    hot_papers = [p for p in hot_papers if get_arxiv_id(p["url"]) not in seen_ids][:max_per_category]

    # arXiv 논문 수집
    categories = config.get("categories", ["cs.CL", "cs.AI", "cs.LG"])
    days_back = args.days if args.days else config.get("days_back", 2)

    print("arXiv에서 논문 수집 중...", file=sys.stderr)
    papers = fetch_arxiv_papers(categories, days_back)

    # 필터링
    filtered_papers = filter_papers(papers, config)

    # 이미 본 논문 제외
    new_papers = [
        p for p in filtered_papers
        if get_arxiv_id(p["url"]) not in seen_ids
    ]
    skipped = len(filtered_papers) - len(new_papers)

    # 카테고리별로 분류 (각 카테고리당 최대 10개)
    categorized = categorize_papers(new_papers, max_per_category)

    # Hot 카테고리 추가
    categorized["hot"] = hot_papers

    # 표시될 총 논문 수 계산
    total_displayed = sum(len(categorized.get(cat_id, [])) for cat_id in CATEGORY_ORDER)

    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_found": len(new_papers) + len(hot_papers),
        "skipped_seen": skipped,
        "displayed": total_displayed,
        "categorized": categorized,
    }

    # 출력 디렉토리 생성
    OUTPUT_DIR.mkdir(exist_ok=True)

    # HTML 생성 및 저장
    html_path = OUTPUT_DIR / "papers.html"
    html_content, ordered_papers = generate_html(result)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # 표시된 논문을 seen에 추가
    today = datetime.now().strftime("%Y-%m-%d")
    for paper in ordered_papers:
        arxiv_id = get_arxiv_id(paper["url"])
        seen["papers"][arxiv_id] = today
    save_seen_papers(seen)

    # JSON 저장 (Claude 요약용) - 순서대로 저장
    result["papers"] = ordered_papers
    json_path = OUTPUT_DIR / "papers.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, ensure_ascii=False, indent=2, fp=f)

    if skipped > 0:
        print(f"이미 본 논문 {skipped}편 제외", file=sys.stderr)
    print(f"총 {len(ordered_papers)}편 표시", file=sys.stderr)
    for cat_id in CATEGORY_ORDER:
        count = len(categorized.get(cat_id, []))
        if count > 0:
            print(f"  - {CATEGORIES[cat_id]['name']}: {count}편", file=sys.stderr)
    print(f"HTML: {html_path}", file=sys.stderr)

    # 브라우저로 열기
    webbrowser.open(f"file://{html_path}")

    # JSON 경로 출력 (Claude가 읽을 수 있도록)
    print(json_path)


if __name__ == "__main__":
    main()
