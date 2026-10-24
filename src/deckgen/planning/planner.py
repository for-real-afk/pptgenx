from __future__ import annotations

from deckgen.llm import LLM
from deckgen.schemas.research import Brief, QuestionPlan

SYSTEM = "You are a research planner for presentation decks. Output JSON only."


def plan(llm: LLM, topic: str, audience: str, slide_count: int, brand: str) -> Brief:
    prompt = (
        f"Topic: {topic}\nAudience: {audience}\nSlides: {slide_count}\n\n"
        "Write a one-sentence objective for the deck and 8-10 specific, answerable research questions that, together, "
        "give the facts a deck of this size needs: origin and context, the key objects or actors, key people and "
        "rivalries or relationships, pivotal events, the turning point, quantitative facts worth charting, and legacy. "
        "Each question names concrete entities (specific models, people, events, years) and asks for checkable facts such as dates, numbers and results. Each question must be searchable on the open web and name concrete entities where the topic allows."
    )
    qp = llm.generate(stage="plan", system=SYSTEM, prompt=prompt, schema=QuestionPlan)
    return Brief(topic=topic, audience=audience, slide_count=slide_count, brand=brand, objective=qp.objective,
                 research_questions=qp.research_questions)
