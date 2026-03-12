"""Translation backend – supports Claude (Anthropic) and Gemini (Google) with Vision API.

Select backend via TRANSLATOR_BACKEND env var:
  TRANSLATOR_BACKEND=claude   (default)
  TRANSLATOR_BACKEND=gemini

Model overrides:
  CLAUDE_MODEL=claude-sonnet-4-6                (default)
  GEMINI_MODEL=gemini-3.1-flash-lite-preview    (default)
"""

import base64
import io
import os
from typing import Literal

from PIL import Image

Backend = Literal["claude", "gemini"]

_claude_client = None
_gemini_client = None

VISION_PROMPT = (
    "이 이미지의 텍스트를 한국어로 번역해 주세요. "
    "번역문만 출력하고 다른 설명은 하지 마세요."
)

MARKDOWN_PROMPT = (
    "이 이미지의 학술 논문 내용을 분석하세요. 아래 규칙을 따르세요:\n"
    "1. 영어 텍스트는 자연스러운 한국어로 번역\n"
    "2. 수학 수식은 LaTeX 형식 그대로 보존 (인라인: $수식$, 블록: $$수식$$)\n"
    "3. 표는 Markdown 표(| 형식)로 재구성\n"
    "4. 제목은 # ## 마크다운 헤딩으로 유지\n"
    "5. 수식 설명 텍스트도 한국어로 번역\n"
    "6. Markdown만 출력, 다른 설명 없음"
)

# 표가 감지된 경우 사용: 표는 플레이스홀더로 남기고 나머지 텍스트만 번역
TABLE_AWARE_PROMPT = (
    "이 이미지의 학술 논문 내용을 분석하세요. 아래 규칙을 따르세요:\n"
    "1. 영어 텍스트는 자연스러운 한국어로 번역\n"
    "2. 수학 수식은 LaTeX 형식 그대로 보존 (인라인: $수식$, 블록: $$수식$$)\n"
    "3. 표(table)는 번역하지 말고 해당 위치에 [TABLE_0], [TABLE_1] 등의 "
    "플레이스홀더만 순서대로 삽입 (표가 1개면 [TABLE_0]만, 2개면 [TABLE_0]과 [TABLE_1])\n"
    "4. 표 제목(캡션)이나 표 주변 텍스트는 한국어로 번역\n"
    "5. 제목은 # ## 마크다운 헤딩으로 유지\n"
    "6. Markdown만 출력, 다른 설명 없음"
)


def get_backend() -> Backend:
    return os.environ.get("TRANSLATOR_BACKEND", "claude").lower().strip()  # type: ignore[return-value]


# ---------------------------------------------------------------- Claude ----

def _get_claude_client():
    global _claude_client
    if _claude_client is None:
        from anthropic import Anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.\n"
                ".env 파일을 확인해 주세요."
            )
        _claude_client = Anthropic(api_key=api_key)
    return _claude_client


def _pil_to_base64_png(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def _claude_vision(image: Image.Image, prompt: str, max_tokens: int = 2048) -> str:
    client = _get_claude_client()
    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    b64_data = _pil_to_base64_png(image)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64_data,
                    },
                },
                {
                    "type": "text",
                    "text": prompt,
                },
            ],
        }],
    )
    return message.content[0].text


# ---------------------------------------------------------------- Gemini ----

def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY 환경변수가 설정되지 않았습니다.\n"
                ".env 파일을 확인해 주세요."
            )
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def _gemini_vision(image: Image.Image, prompt: str) -> str:
    client = _get_gemini_client()
    model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
    response = client.models.generate_content(
        model=model,
        contents=[prompt, image],
    )
    return response.text


# ---------------------------------------------------------- public API ------

def translate_region(image: Image.Image) -> str:
    """Translate text visible in *image* → plain Korean string."""
    backend = get_backend()
    try:
        if backend == "gemini":
            return _gemini_vision(image, VISION_PROMPT)
        else:
            return _claude_vision(image, VISION_PROMPT)
    except Exception as exc:
        return f"[번역 오류 – {backend}] {exc}"


def translate_to_markdown(image: Image.Image) -> str:
    """Analyse *image* → Markdown with translated Korean text, LaTeX math preserved."""
    backend = get_backend()
    try:
        if backend == "gemini":
            return _gemini_vision(image, MARKDOWN_PROMPT)
        else:
            return _claude_vision(image, MARKDOWN_PROMPT, max_tokens=4096)
    except Exception as exc:
        return f"[번역 오류 – {backend}] {exc}"


def translate_to_markdown_table_aware(
    image: Image.Image,
    table_count: int = 0,
    max_tables: int = 5,
) -> str:
    """표 위치에 [TABLE_N] 플레이스홀더를 삽입하는 마크다운 번역.

    AI가 이미지 안에 표가 있는지 스스로 판단하여 [TABLE_0], [TABLE_1] …
    마커를 삽입한다. 표가 없으면 일반 마크다운 번역 결과를 반환한다.

    Args:
        image:       드래그 선택 영역 PIL 이미지
        table_count: (미사용, 하위 호환) 구 버전 호출부를 위한 파라미터
        max_tables:  최대 표 개수 힌트 (프롬프트에 직접 영향 없음)
    """
    backend = get_backend()
    prompt = TABLE_AWARE_PROMPT
    try:
        if backend == "gemini":
            return _gemini_vision(image, prompt)
        else:
            return _claude_vision(image, prompt, max_tokens=4096)
    except Exception as exc:
        return f"[번역 오류 – {backend}] {exc}"


def _parse_layout_json(text: str) -> dict | None:
    """AI 응답 텍스트에서 JSON 파싱. 코드 펜스 제거 후 시도, 실패 시 None."""
    import json
    import re as _re
    # 코드 펜스 제거 (```json ... ``` 또는 ``` ... ```)
    text = _re.sub(r'```(?:json)?\s*', '', text).strip()
    text = _re.sub(r'```\s*$', '', text, flags=_re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        # JSON 블록이 텍스트 중간에 있는 경우 추출 시도
        m = _re.search(r'\{[\s\S]+\}', text)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return None


# 레이아웃 분석 + 번역을 동시에 요청하는 통합 프롬프트
LAYOUT_ANALYSIS_PROMPT = """\
이 이미지를 분석하여 아래 JSON 형식으로만 응답하세요. JSON 외에 아무것도 출력하지 마세요.

{
  "has_tables": <true 또는 false>,
  "layout": "<single | 2col | stacked | mixed>",
  "tables": [
    {
      "id": 0,
      "x_pct": <표의 가장 왼쪽 외곽선 x좌표 ÷ 이미지 전체 너비 × 100. 캡션/제목 포함. 소수점 1자리>,
      "y_pct": <표의 가장 위쪽 외곽선(캡션 포함) y좌표 ÷ 이미지 전체 높이 × 100. 소수점 1자리>,
      "w_pct": <표 전체 너비(오른쪽 외곽선까지) ÷ 이미지 너비 × 100. 소수점 1자리>,
      "h_pct": <표 전체 높이(마지막 행까지) ÷ 이미지 높이 × 100. 소수점 1자리>,
      "caption_ko": "<표 제목을 한국어로 번역>"
    }
  ],
  "markdown": "<나머지 텍스트를 한국어로 번역. 각 표 위치에 [TABLE_0], [TABLE_1] 등을 단독 문단으로 삽입 (앞뒤 빈 줄). 수식은 $...$, $$...$$ 원본 LaTeX 유지. Markdown 형식>"
}

좌표 측정 기준:
- 이미지 좌상단이 (0, 0), 우하단이 (100, 100)
- x_pct + w_pct ≤ 100, y_pct + h_pct ≤ 100 이어야 함
- 표 캡션(Table 1: ...)이 있으면 반드시 y_pct에 포함 (캡션 위쪽 라인부터 시작)
- 표 외곽선(border)의 바깥쪽을 기준으로 측정 (안쪽 셀 기준 아님)
- 좌우 여백(margin)은 포함하지 않음 (표 자체 영역만)
- 2col 레이아웃: 두 표가 좌우 나란히 있을 때 각 표를 개별 bounding box로 측정

layout 값:
- single  : 표 1개
- 2col    : 표 2개가 좌우 나란히 (2단 편집)
- stacked : 표 2개+ 위아래 배치
- mixed   : 3개+ 또는 복잡한 배치

표가 없으면 has_tables=false, tables=[], layout="single", markdown에 전체 번역 결과 출력.
"""


def get_layout_model_backend() -> tuple[str, str]:
    """레이아웃 분석용 백엔드와 모델을 반환.

    LAYOUT_MODEL 환경변수가 설정된 경우 해당 모델 사용.
    미설정 시 기본 번역 모델 그대로 사용.

    Returns:
        (backend, model_name) 튜플  — backend: "claude" | "gemini"
    """
    layout_model = os.environ.get("LAYOUT_MODEL", "").strip()
    if not layout_model:
        backend = get_backend()
        if backend == "gemini":
            return "gemini", os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        else:
            return "claude", os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

    # 모델명으로 백엔드 자동 판별
    if "gemini" in layout_model.lower():
        return "gemini", layout_model
    else:
        return "claude", layout_model


def _gemini_vision_with_model(image: Image.Image, prompt: str, model: str) -> str:
    client = _get_gemini_client()
    response = client.models.generate_content(model=model, contents=[prompt, image])
    return response.text


def _claude_vision_with_model(
    image: Image.Image, prompt: str, model: str, max_tokens: int = 2048
) -> str:
    client = _get_claude_client()
    b64_data = _pil_to_base64_png(image)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64_data,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return message.content[0].text


def analyze_and_translate(image: Image.Image) -> dict:
    """레이아웃 분석 + 번역을 1회 API 호출로 수행.

    AI가 이미지의 표 레이아웃(위치·배치)을 분석하고 번역까지 한번에 반환한다.
    LAYOUT_MODEL 환경변수로 번역 모델과 다른 강력한 모델을 지정할 수 있다.

    Returns:
        {
          "has_tables": bool,
          "layout": str,      # "single" | "2col" | "stacked" | "mixed"
          "tables": list,     # [{id, x_pct, y_pct, w_pct, h_pct, caption_ko}, ...]
          "markdown": str,    # [TABLE_N] 마커 포함 번역 마크다운
        }
        JSON 파싱 실패 시 has_tables=False, markdown=raw 응답 텍스트
    """
    backend, model = get_layout_model_backend()
    try:
        raw = (
            _gemini_vision_with_model(image, LAYOUT_ANALYSIS_PROMPT, model)
            if backend == "gemini"
            else _claude_vision_with_model(image, LAYOUT_ANALYSIS_PROMPT, model, max_tokens=4096)
        )
    except Exception as exc:
        return {
            "has_tables": False,
            "layout": "single",
            "tables": [],
            "markdown": f"[번역 오류 – {backend}/{model}] {exc}",
        }

    data = _parse_layout_json(raw)
    if data and "markdown" in data:
        return {
            "has_tables": bool(data.get("has_tables", False)),
            "layout":     data.get("layout", "single"),
            "tables":     data.get("tables", []),
            "markdown":   data["markdown"],
        }

    # JSON 파싱 실패 → raw 텍스트 자체를 마크다운으로 fallback
    return {
        "has_tables": False,
        "layout": "single",
        "tables": [],
        "markdown": raw,
    }


def reset_clients() -> None:
    """Force re-initialisation of API clients (e.g. after env change)."""
    global _claude_client, _gemini_client
    _claude_client = None
    _gemini_client = None
