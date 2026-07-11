"""
사용자 질문 의도 파싱 + 자연어 응답 생성
"""
import json
import logging

from modules.summarizer import llm_answer

logger = logging.getLogger(__name__)


def parse_intent(question):
    prompt = f"""사용자 질문을 분석하여 JSON으로만 응답하세요. 다른 텍스트 없이.

{{"tickerName":"종목명 또는 null","category":"COMPANY|INDUSTRY|MARKET|ECONOMY|DEBENTURE 또는 null",
"periodDays":기간일수(기본30),"metric":"UP|DOWN|SAME|NEW|ALL|SUMMARY",
"opinionType":"BUY|HOLD|SELL 또는 null"}}

참고: "상향/올린/상승"→UP, "하향/내린/하락"→DOWN, "신규/새로/처음"→NEW
"최근 한달"→30, "일주일"→7, "3개월"→90, 없으면 30

질문: {question}"""

    result = llm_answer(prompt, temperature=0.2)

    try:
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        intent = json.loads(cleaned.strip())
    except(json.JSONDecodeError, ValueError):
        try:
            s = result.index("{")
            e = result.rindex("}") + 1
            intent = json.loads(result[s:e])
        except(json.JSONDecodeError, ValueError):
            intent = {"tickerName": None, "category": None,
                       "periodDays": 30, "metric": "ALL", "opinionType": None}

    intent.setdefault("periodDays", 30)
    intent.setdefault("metric", "ALL")
    logger.info(f"질문: {question} → {intent}")
    return intent


def generate_answer(question, db_result, intent):
    prompt = f"""다음 데이터 기반으로 한국어 1~3문장 답변. 간결하게.
결과 0건이면 "해당 기간 내 관련 리포트가 없습니다."

질문: {question}
의도: {json.dumps(intent, ensure_ascii=False)}
DB결과: {json.dumps(db_result, ensure_ascii=False)}"""

    return llm_answer(prompt, temperature=0.5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for q in ["최근 한달간 삼성전자 상향 몇건?", "이번주 산업분석 요약해줘"]:
        print(f"Q: {q}")
        print(f"A: {parse_intent(q)}\n")
