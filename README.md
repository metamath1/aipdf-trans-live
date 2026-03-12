# AI PDF 번역기 (aipdf-trans-live)

학술 PDF 논문을 읽으면서 마우스로 영역을 드래그하면, 수식(LaTeX)까지 포함한 번역 결과를 앱 내 오른쪽 패널에 **렌더링된 PDF**로 바로 표시해 주는 데스크탑 애플리케이션입니다.

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/License-MIT-green)

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| **드래그 번역** | PDF 위에서 마우스를 드래그하면 선택 영역이 Vision LLM으로 전송되어 자동 번역 |
| **수식 렌더링** | LaTeX 수식(`$...$`, `$$...$$`, `\(...\)`, `\[...\]`)을 MathJax로 렌더링하여 PDF로 표시 |
| **인라인 PDF 표시** | Playwright + 시스템 Edge/Chrome으로 HTML→PDF를 생성, 앱 내 우측 패널에 직접 표시 |
| **Markdown 모드** | 번역 결과를 구조화된 Markdown(표, 제목, 코드블록, 수식)으로 출력 |
| **텍스트 모드** | 간단한 텍스트 번역 결과만 표시 |
| **다중 번역 엔진** | Claude (Anthropic) 또는 Gemini (Google) 선택 가능 |
| **페이지 탐색** | 마우스 휠 스크롤, 화살표/PgUp/PgDn 키보드 단축키, 페이지 버튼 |
| **줌 컨트롤** | 좌측 PDF 뷰어 및 우측 번역 패널 모두 독립적으로 확대/축소 |
| **자동 폭 맞춤** | 창 크기 변경(최대화 포함) 시 양쪽 패널의 PDF가 자동으로 패널 폭에 맞게 조정 |
| **브라우저 열기** | 번역 결과를 시스템 브라우저에서 별도로 열어 확인 |

---

## 스크린샷

```
┌──────────────────────────┬──────────────────────────┐
│  [◀이전] [다음▶] 확대/축소│  [Claude▌] 번역 완료      │
│                          │  [확대+] [축소−] [폭맞춤] │
│   ← PDF 뷰어 →           │                          │
│   (마우스 드래그로        │   ← 번역 결과 PDF →      │
│    번역 영역 선택)        │   (수식 렌더링 포함)      │
│                          │                          │
│                          │  [🌐 브라우저에서 열기]   │
└──────────────────────────┴──────────────────────────┘
```

---

## 사전 요구사항

- **[uv](https://docs.astral.sh/uv/)** 패키지 매니저
- **Python 3.10+**
- **번역 API 키** (둘 중 하나 이상)
  - Anthropic API Key → [console.anthropic.com](https://console.anthropic.com/)
  - Google Gemini API Key → [aistudio.google.com](https://aistudio.google.com/app/apikey)
- **Microsoft Edge 또는 Chrome** (수식 렌더링 PDF 생성용, 시스템에 설치된 브라우저 자동 사용)

---

## 설치 및 실행

```bash
# 1. 저장소 클론
git clone https://github.com/metamath1/aipdf-trans-live.git
cd aipdf-trans-live

# 2. 의존성 설치 (uv가 가상환경 자동 생성)
uv sync

# 3. 환경변수 설정
cp .env.example .env
# .env 파일을 열어 API 키 입력

# 4. Playwright 브라우저 드라이버 설치 (최초 1회, ~2MB)
uv run playwright install msedge

# 5. 앱 실행
uv run main.py
```

> **PDF 폴더 지정**: `uv run main.py /path/to/pdfs` 로 PDF 저장 폴더를 변경할 수 있습니다.
> 기본값은 프로젝트 루트의 `pdfs/` 폴더입니다.

---

## 환경변수 설정 (.env)

```ini
# 번역 엔진: claude (기본값) 또는 gemini
TRANSLATOR_BACKEND=claude

# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx...
# CLAUDE_MODEL=claude-sonnet-4-6   # 기본값, 변경 시 주석 해제

# Google Gemini
GEMINI_API_KEY=AIzaSy...
# GEMINI_MODEL=gemini-2.0-flash    # 기본값, 변경 시 주석 해제
```

---

## 사용 방법

1. **PDF 열기** — 상단 `[PDF 열기]` 버튼으로 PDF 파일을 선택합니다.
2. **영역 드래그** — 번역하고 싶은 텍스트·수식 위에서 마우스 왼쪽 버튼을 누른 채 드래그합니다.
3. **번역 확인** — 우측 패널에 수식이 렌더링된 번역 PDF가 자동으로 표시됩니다.
4. **모드 전환** — 상단 `Markdown` / `텍스트` 라디오 버튼으로 출력 형식을 전환합니다.

### 키보드 / 마우스 단축키 (PDF 뷰어 클릭 후 활성화)

| 입력 | 동작 |
|---|---|
| `마우스 휠` | 페이지 스크롤, 끝/처음에서 다음/이전 페이지로 이동 |
| `↑ / ↓` | 위아래 스크롤 |
| `← / →` | 이전 / 다음 페이지 |
| `PgUp / PgDn` | 이전 / 다음 페이지 |

---

## 프로젝트 구조

```
aipdf-trans-live/
├── main.py                    # 앱 진입점 – 창 레이아웃, 번역 엔진 전환
├── src/
│   ├── pdf_viewer.py          # PDF 렌더러 (PyMuPDF) + 드래그 선택 + 자동 폭맞춤
│   ├── translator.py          # Claude / Gemini Vision API 번역 (이미지 → Markdown)
│   ├── renderer.py            # Markdown+LaTeX → HTML (MathJax) → PDF bytes (Playwright)
│   ├── translation_panel.py   # 우측 번역 패널 – PDF 캔버스 + 줌 컨트롤
│   └── file_browser.py        # PDF 파일 브라우저 (확장용)
├── pdfs/                      # 번역할 PDF 파일 저장 폴더
├── .env.example               # 환경변수 샘플
├── pyproject.toml             # 프로젝트 메타데이터 및 의존성
└── uv.lock                    # 재현 가능한 의존성 잠금 파일
```

---

## 기술 스택

| 역할 | 라이브러리 |
|---|---|
| GUI 프레임워크 | tkinter + [ttkbootstrap](https://ttkbootstrap.readthedocs.io/) (darkly 테마) |
| PDF 렌더링 | [PyMuPDF (fitz)](https://pymupdf.readthedocs.io/) |
| 이미지 처리 | [Pillow](https://pillow.readthedocs.io/) |
| Markdown → HTML | [mistune](https://mistune.lepture.com/) 3.x |
| 수식 렌더링 | [MathJax](https://www.mathjax.org/) 3 (CDN) |
| HTML → PDF | [Playwright](https://playwright.dev/python/) (headless Edge/Chrome) |
| Claude 번역 | [anthropic](https://github.com/anthropics/anthropic-sdk-python) SDK |
| Gemini 번역 | [google-genai](https://github.com/google-gemini/generative-ai-python) SDK |
| 환경변수 | [python-dotenv](https://github.com/theskumar/python-dotenv) |

---

## 아키텍처 흐름

```
마우스 드래그 선택
    │
    ▼
PIL 이미지 크롭
    │
    ▼
Vision LLM (Claude / Gemini)
    │  이미지 → Markdown + LaTeX
    ▼
_extract_math()          ← $...$, $$...$$, \(...\), \[...\] 를 플레이스홀더로 보호
    │
    ▼
mistune.html()           ← Markdown → HTML (플레이스홀더는 안전하게 통과)
    │
    ▼
_restore_math()          ← 플레이스홀더를 원본 LaTeX 구분자로 복원 (verbatim)
    │
    ▼
HTML + MathJax 설정
    │
    ▼
Playwright (headless Edge/Chrome)
    │  MathJax 렌더 완료 대기 → page.pdf()
    ▼
PDF bytes → PyMuPDF → PIL Image → tkinter Canvas
```

---

## 라이선스

MIT License
