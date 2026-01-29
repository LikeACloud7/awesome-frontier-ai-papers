#!/usr/bin/env python3
"""
논문 리뷰 스크립트
선택된 논문들을 Gemini CLI 또는 Claude로 핵심 정리
"""

import json
import subprocess
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent / "output"
PAPERS_JSON = OUTPUT_DIR / "papers.json"
PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "review_paper.md"
CONFIG_FILE = Path(__file__).parent.parent / "config" / "interests.json"


def load_config() -> dict:
    """설정 파일 로드"""
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_papers() -> dict:
    """papers.json 로드"""
    with open(PAPERS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt_template() -> str:
    """프롬프트 템플릿 로드"""
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read()


def review_paper_with_gemini(paper: dict, paper_num: int) -> dict:
    """Gemini CLI로 논문 리뷰"""
    url = paper["url"]
    title = paper["title"]

    # 프롬프트 템플릿 로드 및 URL 치환
    prompt_template = load_prompt_template()
    prompt = prompt_template.replace("{url}", url)

    try:
        # Gemini CLI 호출 (--yolo로 자동 승인, positional prompt 사용)
        result = subprocess.run(
            ["gemini", "--yolo", prompt],
            capture_output=True,
            text=True,
            timeout=180  # 3분 타임아웃
        )

        if result.returncode == 0:
            review = result.stdout.strip()
        else:
            review = f"리뷰 실패: {result.stderr}"

    except subprocess.TimeoutExpired:
        review = "리뷰 시간 초과 (3분)"
    except FileNotFoundError:
        review = "Gemini CLI를 찾을 수 없습니다. 설치를 확인해주세요."
    except Exception as e:
        review = f"리뷰 실패: {str(e)}"

    return {
        "num": paper_num,
        "title": title,
        "url": url,
        "review": review,
        "orgs": paper.get("matched_orgs", []),
    }


def generate_review_html(reviews: list[dict], date: str) -> str:
    """리뷰 결과 HTML 생성 (탭 UI)"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>논문 리뷰 - {date}</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1000px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
            line-height: 1.6;
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #4a90d9;
            padding-bottom: 10px;
        }}
        .stats {{
            color: #666;
            margin-bottom: 20px;
        }}
        .controls {{
            background: #4a90d9;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
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
            margin-right: 10px;
        }}
        .controls button:hover {{ background: #e8e8e8; }}
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
            max-width: 200px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .tab:hover {{ background: #ccc; }}
        .tab.active {{
            background: white;
            color: #333;
            font-weight: 600;
        }}
        .tab .num {{
            background: #4a90d9;
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 12px;
            margin-right: 8px;
        }}
        .tab.active .num {{ background: #333; }}
        .tab-content {{
            display: none;
            background: white;
            border-radius: 0 8px 8px 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            padding: 30px;
            min-height: 500px;
        }}
        .tab-content.active {{ display: block; }}
        .review-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #eee;
        }}
        .review-title {{
            font-size: 20px;
            font-weight: 600;
            color: #333;
            flex: 1;
        }}
        .review-title a {{
            color: inherit;
            text-decoration: none;
        }}
        .review-title a:hover {{
            color: #4a90d9;
        }}
        .org-tag {{
            background: #e3f2fd;
            color: #1976d2;
            padding: 5px 12px;
            border-radius: 4px;
            font-size: 13px;
            font-weight: 600;
        }}
        .review-content {{
            color: #444;
        }}
        .review-content h2 {{
            color: #333;
            font-size: 18px;
            margin-top: 25px;
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 1px solid #eee;
        }}
        .review-content h3 {{
            color: #4a90d9;
            font-size: 15px;
            margin-top: 20px;
            margin-bottom: 10px;
        }}
        .review-content p {{
            margin: 10px 0;
        }}
        .review-content ul {{
            margin: 10px 0;
            padding-left: 25px;
        }}
        .review-content li {{
            margin: 6px 0;
        }}
        .review-content strong {{
            color: #333;
        }}
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
        .toast.show {{ display: block; }}
    </style>
</head>
<body>
    <h1>📚 논문 리뷰</h1>
    <p class="stats">{date} | {len(reviews)}편 리뷰 완료</p>

    <div class="controls">
        <button onclick="copyLinks()">🔗 전체 링크 복사 (NotebookLM용)</button>
    </div>

    <div class="tabs">
"""

    # 탭 버튼 생성
    for i, review in enumerate(reviews):
        active_class = "active" if i == 0 else ""
        short_title = review['title'][:25] + "..." if len(review['title']) > 25 else review['title']
        html += f"""        <button class="tab {active_class}" onclick="showTab({i})" data-tab="{i}">
            <span class="num">{review['num']}</span>{short_title}
        </button>
"""

    html += """    </div>
"""

    # 탭 컨텐츠 생성
    for i, review in enumerate(reviews):
        active_class = "active" if i == 0 else ""
        org_tags = "".join([f'<span class="org-tag">{org}</span>' for org in review["orgs"]])

        html += f"""
    <div class="tab-content {active_class}" id="tab-{i}" data-url="{review['url']}">
        <div class="review-header">
            {org_tags}
            <span class="review-title">
                <a href="{review['url']}" target="_blank">{review['title']}</a>
            </span>
        </div>
        <div class="review-content">
            {markdown_to_html(review['review'])}
        </div>
    </div>
"""

    html += """
    <div id="toast" class="toast"></div>

    <script>
        function showTab(index) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab[data-tab="${index}"]`).classList.add('active');
            document.getElementById(`tab-${index}`).classList.add('active');
        }

        function copyLinks() {
            const links = Array.from(document.querySelectorAll('.tab-content'))
                .map(r => r.dataset.url)
                .join('\\n');
            navigator.clipboard.writeText(links);
            showToast('링크 복사됨!');
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
    return html


def markdown_to_html(text: str) -> str:
    """간단한 마크다운 → HTML 변환"""
    import re

    # 헤더
    text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)

    # 볼드
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)

    # 리스트
    text = re.sub(r'^- (.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
    text = re.sub(r'(<li>.*</li>\n?)+', r'<ul>\g<0></ul>', text)

    # 줄바꿈
    text = re.sub(r'\n\n', '</p><p>', text)
    text = f'<p>{text}</p>'
    text = re.sub(r'<p>\s*</p>', '', text)
    text = re.sub(r'<p>\s*<h', '<h', text)
    text = re.sub(r'</h(\d)>\s*</p>', r'</h\1>', text)
    text = re.sub(r'<p>\s*<ul>', '<ul>', text)
    text = re.sub(r'</ul>\s*</p>', '</ul>', text)

    return text


def output_for_claude(selected: list) -> None:
    """Claude 리뷰를 위한 논문 정보 출력"""
    prompt_template = load_prompt_template()

    output = {
        "mode": "claude_review",
        "prompt_template": prompt_template,
        "papers": []
    }

    for num, paper in selected:
        output["papers"].append({
            "num": num,
            "title": paper["title"],
            "url": paper["url"],
            "abstract": paper.get("abstract", ""),
            "authors": paper.get("authors", []),
            "orgs": paper.get("matched_orgs", []),
        })

    # JSON으로 출력 (Claude가 파싱할 수 있도록)
    print(json.dumps(output, ensure_ascii=False, indent=2))


def main():
    """메인 함수"""
    if len(sys.argv) < 2:
        print("사용법: python review_papers.py 1,3,7", file=sys.stderr)
        sys.exit(1)

    # 논문 번호 파싱
    numbers_str = sys.argv[1].replace(" ", "")
    try:
        numbers = [int(n.strip()) for n in numbers_str.split(",") if n.strip()]
    except ValueError:
        print("잘못된 번호 형식입니다.", file=sys.stderr)
        sys.exit(1)

    # papers.json 로드
    data = load_papers()
    papers = data.get("papers", [])

    # 선택된 논문 필터링
    selected = []
    for num in numbers:
        if 1 <= num <= len(papers):
            selected.append((num, papers[num - 1]))
        else:
            print(f"경고: {num}번 논문이 없습니다.", file=sys.stderr)

    if not selected:
        print("선택된 논문이 없습니다.", file=sys.stderr)
        sys.exit(1)

    # 설정에서 리뷰 제공자 확인
    config = load_config()
    review_provider = config.get("review_provider", "gemini")

    if review_provider == "claude":
        # Claude 모드: 논문 정보를 JSON으로 출력
        output_for_claude(selected)
        return

    # Gemini 모드: 병렬로 Gemini CLI 호출
    print(f"{len(selected)}편 논문 리뷰 시작 (Gemini CLI)...", file=sys.stderr)

    reviews = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(review_paper_with_gemini, paper, num): num
            for num, paper in selected
        }

        for future in as_completed(futures):
            num = futures[future]
            try:
                review = future.result()
                reviews.append(review)
                print(f"  ✓ {num}번 완료", file=sys.stderr)
            except Exception as e:
                print(f"  ✗ {num}번 실패: {e}", file=sys.stderr)

    # 번호순 정렬
    reviews.sort(key=lambda r: r["num"])

    # HTML 생성
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = generate_review_html(reviews, date)

    # 저장
    html_path = OUTPUT_DIR / "reviews.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n리뷰 완료! {html_path}", file=sys.stderr)

    # 브라우저로 열기
    webbrowser.open(f"file://{html_path}")


if __name__ == "__main__":
    main()
