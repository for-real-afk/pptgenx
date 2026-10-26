"""Dry cost estimate before any spend. UPPER bound: every call at its planned size, every retry/fix round used, no cache savings, output at max_tokens.
Expected is the same plan with one writer pass, no fix rounds and about half the output. Input sizes are fixed assumptions (see ASSUME), not tuned to a topic."""
from __future__ import annotations

from deckgen.config import CONFIG, ROLE_MAX_TOKENS
from deckgen.ledger import Prices

ASSUME = {"query_in": 600, "extract_in": 3500, "pair_in": 3000, "outline_in": 14000, "write_in": 18000, "check_in": 4000, "fix_in": 6000,
          "critic_in": 24000, "questions": 8, "conflict_pairs": 20}


def estimate(slides: int, roles: dict, prices: Prices) -> dict:
    a = ASSUME
    qa_rounds, fix, retries = CONFIG.qa_max_rounds, CONFIG.fix_rounds, CONFIG.spec_retries
    # (stage, role, calls_upper, calls_expected, input_tokens)
    plan = [
        ("plan", "extractor", 1, 1, 1500),
        ("research queries", "extractor", a["questions"] * 3, a["questions"], a["query_in"]),
        ("research extraction", "extractor", int(CONFIG.max_pages * 1.5), CONFIG.max_pages, a["extract_in"]),
        ("conflict judging", "checker", a["conflict_pairs"], a["conflict_pairs"] // 2, a["pair_in"]),
        ("outline", "writer", retries + 1, 1, a["outline_in"]),
        ("slide writing", "writer", slides * 2, slides, a["write_in"]),
        ("slide edit loop", "writer", slides * fix, slides // 3, a["fix_in"]),
        ("faithfulness/consistency checks", "checker", slides * (2 + fix) + 3, slides + 3, a["check_in"]),
        ("visual critic", "critic", qa_rounds * 2, 2, a["critic_in"]),
        ("QA fixer", "writer", qa_rounds * 3, 2, a["fix_in"]),
    ]
    rows, up, ex = [], 0.0, 0.0
    for name, role, cu, ce, tin in plan:
        spec = roles[role]
        p = prices.price(spec.model)
        mo = ROLE_MAX_TOKENS[role]
        hi = cu * (tin * max(p.input, p.cache_write) + mo * p.output) / 1e6
        lo = ce * (tin * p.input + 0.5 * mo * p.output) / 1e6
        rows.append((name, f"{spec.provider}:{spec.model}", cu, round(hi, 2), round(lo, 2)))
        up, ex = up + hi, ex + lo
    return {"upper_usd": round(up, 2), "expected_usd": round(ex, 2), "rows": rows}


def table(e: dict) -> str:
    out = ["| stage | model | calls (upper) | upper USD | expected USD |", "|---|---|---|---|---|"]
    out += [f"| {n} | {m} | {c} | {u} | {x} |" for n, m, c, u, x in e["rows"]]
    return "\n".join(out) + f"\n\nUPPER ${e['upper_usd']}  EXPECTED ~${e['expected_usd']} (cap ${CONFIG.budget_usd})"
