"""
LLM 요약 모듈 - Ollama (qwen3:8b) 직접 호출
"""

import json
import logging
import os

import yaml
from openai import OpenAI

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)

# 수정: 클라이언트 초기화를 함수 내부로 이동
MODEL = config["ollama"]["model"]

def _get_client():
    """지연 초기화"""
    return OpenAI(
        base_url=config["ollama"]["api_base"],
        api_key="ollama",
    )
PDF_MAX_PAGES = config["crawl"]["pdf_max_pages"]
PDF_MAX_CHARS = config["crawl"]["pdf_max_chars"]


def llm_answer(prompt, temperature=0.7):
    try:
        # 수정: 함수 호출 시점에 클라이언트 생성
        client = _get_client()
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"LLM 오류: {e}")
        return ""




def summarize_report(pdf_text, category):
    if not pdf_text:
        return {}

    prompts = {
        "COMPANY": """다음 종목분석 리포트를 JSON으로만 응답하세요. 다른 텍스트 없이.
{{"tickerName":"종목명","tickerCode":"코드 또는 null","investmentOpinion":"BUY|HOLD|SELL",
"targetPrice":목표주가,"prevTargetPrice":직전목표주가 또는 null,
"summary":"핵심 3줄 요약","keywords":"키워드3개"}}

본문:
{text}""",

        "INDUSTRY": """다음 산업분석 리포트를 JSON으로만 응답하세요. 다른 텍스트 없이.
{{"title":"산업명","trend":"트렌드 요약","benefitTickers":"수혜종목",
"summary":"핵심 3줄 요약","keywords":"키워드3개"}}

본문:
{text}""",

        "DEFAULT": """다음 {label} 리포트를 JSON으로만 응답하세요. 다른 텍스트 없이.
{{"title":"주제","summary":"핵심 3줄 요약","outlook":"전망 1줄","keywords":"키워드3개"}}

본문:
{text}""",
    }

    label_map = {"MARKET": "시황정보", "ECONOMY": "경제분석", "DEBENTURE": "채권분석"}

    if category in prompts:
        prompt = prompts[category].format(text=pdf_text)
    else:
        prompt = prompts["DEFAULT"].format(text=pdf_text, label=label_map.get(category, "분석"))

    result = llm_answer(prompt, temperature=0.3)
    return _parse_json(result)


def _parse_json(text):
    if not text:
        return {}
    try:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        return json.loads(cleaned.strip())
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"Failed to parse cleaned summary text: {e}")



if __name__ == "__main__":
    print(llm_answer("안녕하세요. 테스트입니다. 짧게 답변하세요."))
