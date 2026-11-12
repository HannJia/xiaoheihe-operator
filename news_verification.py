"""Fail-closed evidence, chronology and event gates after independent review."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from typing import Any
try:
    from . import news_sources
except ImportError:
    import news_sources

VERSION = 1
MAX_AGE = 36 * 3600
FUTURE_SLACK = 2 * 3600


def text(value) -> str:
    return news_sources.normalized(value)


def entity(value) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text(value)).casefold())


def signature(subjects: list[str], action: str, stage: str = "") -> dict[str, Any]:
    return {"subjects": sorted({entity(x) for x in subjects if 3 <= len(entity(x)) <= 100}),
            "action": action, "stage": stage}


def same_event(a: dict, b: dict) -> bool:
    if a.get("action") != b.get("action"): return False
    if a.get("action") == "acquisition" and {a.get("stage"), b.get("stage")} == {"announcement", "closed"}:
        return False
    first, second = set(a.get("subjects") or []), set(b.get("subjects") or [])
    return bool(first and second and len(first & second) / len(first | second) >= .5)


def quote_valid(quote: object, document: dict, *, date=False) -> bool:
    q = text(quote)
    body = text(document.get("date_text") if date else document.get("content"))
    return 12 <= len(q) <= 600 and q in body


def document_for(source: object, docs: list[dict]) -> dict:
    if isinstance(source, str):
        return next((doc for doc in docs if doc.get("source_id") == source), {})
    # Integer indices remain useful to offline clients without named documents.
    # Named protocol never guesses/corrects a mismatched index or crosses news IDs.
    if not any(doc.get("source_id") for doc in docs) and type(source) is int and 1 <= source <= len(docs):
        return docs[source - 1]
    return {}


def cited_quotes(values: object, docs: list[dict]) -> list[dict]:
    if not isinstance(values, list) or not values: return []
    output = []
    for item in values:
        if not isinstance(item, dict): return []
        doc = document_for(item.get("source"), docs)
        if not news_sources.source_kind(doc.get("url", "")) or not quote_valid(item.get("quote"), doc):
            return []
        output.append({"source_url": doc["url"], "quote": text(item["quote"])})
    return output


def eligible_time(doc: dict, now: float) -> str:
    interval = doc.get("published_interval")
    if not isinstance(interval, list) or len(interval) != 2:
        return "original_time_missing"
    try: start, end = map(float, interval)
    except (TypeError, ValueError): return "original_time_missing"
    if start > end or start > now + FUTURE_SLACK: return "future_event"
    end = min(end, now)
    if end < now - MAX_AGE: return "event_stale"
    if start < now - MAX_AGE: return "event_time_uncertain"
    return ""


def gate(written: dict, picked: list[dict], review: object, *, now: float,
         recent: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """Return filtered writing, audit records, removals. Never repair/rewrite a fact."""
    removed, accepted, audits = [], [], []
    if not isinstance(review, dict) or not isinstance(review.get("items"), list):
        raise ValueError("independent_review_unavailable")
    verdicts = {}
    for value in review["items"]:
        if isinstance(value, dict) and type(value.get("id")) is int:
            index = value["id"]
            if index in verdicts: verdicts[index] = None
            else: verdicts[index] = value
    seen = [r.get("event", {}) for r in recent]
    accepted_ids = set()
    for entry in written.get("items") or []:
        if not isinstance(entry, dict) or type(entry.get("id")) is not int: continue
        index = entry["id"]
        if index in accepted_ids or not 1 <= index <= len(picked): continue
        accepted_ids.add(index)
        item, verdict = picked[index - 1], verdicts.get(index)
        title = item.get("title") or text(entry.get("fact"))[:60]
        reason = ""
        docs = item.get("documents") or []
        if not isinstance(verdict, dict):
            reason = "review_missing"
        elif verdict.get("verdict") != "supported" or verdict.get("atomic") is not True:
            reason = "semantic_rejected:" + text(verdict.get("reason"))[:150]
        elif isinstance(verdict.get("duplicate_of"), str) and verdict["duplicate_of"].startswith("recent:"):
            reason = "event_duplicate"
        elif text(verdict.get("fact_checked")) != text(entry.get("fact")):
            reason = "review_wrong_fact"
        quotes = cited_quotes(verdict.get("claims") if verdict else None, docs)
        if not reason and not quotes: reason = "evidence_missing"
        if not reason and all(news_sources.source_kind(q["source_url"]) == "report" for q in quotes):
            if not re.search(r"据.{0,18}(?:报道|透露|消息)|报道称|according to|reported", text(entry.get("fact")), re.I):
                reason = "report_attribution"
        if not reason:
            for citation in quotes:
                cited = next(d for d in docs if d.get("url") == citation["source_url"])
                reason = eligible_time(cited, now)
                if reason: break
        event = verdict.get("event") if verdict else None
        if not reason and not isinstance(event, dict): reason = "event_missing"
        event = event if isinstance(event, dict) else {}
        source_id = event.get("source")
        doc = document_for(source_id, docs)
        if not reason:
            reason = eligible_time(doc, now)
            if not reason and doc.get("url") not in {q["source_url"] for q in quotes}:
                reason = "event_date_evidence"
        if not reason:
            date_quote = event.get("date_quote")
            if event.get("date_basis") == "first_publication":
                if not quote_valid(date_quote, doc, date=True): reason = "event_date_evidence"
            elif event.get("date_basis") == "explicit_event_date":
                if not quote_valid(date_quote, doc): reason = "event_date_evidence"
            else: reason = "event_date_evidence"
        event_date = news_sources.parse_date(event.get("date"), doc.get("url", ""))
        if not reason:
            if not event_date: reason = "event_date_evidence"
            else:
                start, end = event_date
                if end < now - MAX_AGE: reason = "event_stale"
                elif start > now + FUTURE_SLACK: reason = "future_event"
                elif event.get("date_basis") == "first_publication":
                    pub_start, pub_end = doc["published_interval"]
                    if end < pub_start - 1 or start > pub_end + 1: reason = "event_date_evidence"
        subjects = event.get("subjects")
        event_key = signature(subjects, event.get("action"), event.get("stage") if event.get("stage") in (
            "announcement", "preview", "available", "closed", "update", "other") else "") if isinstance(subjects, list) and all(isinstance(x,str) for x in subjects) and isinstance(event.get("action"),str) else {}
        if not reason and (not event_key.get("subjects") or event_key.get("action") not in {
                "release", "acquisition", "personnel", "research", "pricing", "availability", "other"}):
            reason = "event_missing"
        if not reason and any(same_event(event_key, old) for old in seen): reason = "event_duplicate"
        if reason:
            removed.append({"title": title, "reason": reason})
            continue
        final = dict(entry)
        comment_status = verdict.get("comment_verdict")
        if final.get("comment") and (comment_status not in ("opinion", "supported") or
                text(verdict.get("comment_checked")) != text(final["comment"]) or
                (comment_status == "supported" and not cited_quotes(verdict.get("comment_claims"), docs))):
            final["comment"] = ""
            removed.append({"title": title, "reason": "comment_semantic_rejected:" + text(verdict.get("comment_reason"))[:100]})
        # All citations must be to the actual primary/report documents, not the discovery snippet.
        primary = next(d for d in docs if d["url"] == quotes[0]["source_url"])
        item.update(url=primary["url"], content=primary["content"],
                    published=primary["published_interval"][0],
                    title=primary.get("title") or item.get("title"), review_id=index)
        item["verified_event"] = event_key
        item["verified_evidence"] = quotes
        final["evidence"] = quotes[0]["quote"]
        accepted.append(final)
        audits.append({"id": index, "event": event_key, "event_date": event["date"],
                       "source_url": primary["url"], "published_raw": primary.get("published_raw"),
                       "content_sha256": hashlib.sha256(primary["content"].encode()).hexdigest(),
                       "claims": quotes, "comment_kept": bool(final.get("comment")),
                       "batch_duplicate_of": verdict.get("duplicate_of")})
        seen.append(event_key)
    # Semantic duplicate links only join candidates which actually passed all
    # evidence/time checks. A rejected wrong sentence cannot suppress its correction.
    valid_ids = {a["id"] for a in audits}
    parents = {index: index for index in valid_ids}
    def parent(index):
        while parents[index] != index: index = parents[index]
        return index
    for audit in audits:
        raw = audit.pop("batch_duplicate_of", None)
        target = raw if type(raw) is int else int(raw) if isinstance(raw,str) and raw.isdigit() else None
        if target in valid_ids:
            a,b=parent(audit["id"]),parent(target)
            parents[max(a,b)] = min(a,b)
    duplicate_ids = {index for index in valid_ids if parent(index) != index}
    for entry in accepted:
        if entry["id"] in duplicate_ids:
            removed.append({"title":picked[entry["id"]-1].get("title", ""),"reason":"event_duplicate"})
    accepted = [entry for entry in accepted if entry["id"] not in duplicate_ids]
    audits = [audit for audit in audits if audit["id"] not in duplicate_ids]
    intro = written.get("intro", "") if review.get("intro_supported") is True else ""
    take = written.get("take", "") if review.get("take_supported") is True else ""
    # A conclusion approved against later-discarded facts cannot survive.
    if len(accepted) != len(written.get("items") or []): take = ""
    return {"intro": intro, "items": accepted, "take": take}, audits, removed


def seal(blocks: list[str], audits: list[dict]) -> dict:
    return {"version": VERSION, "status": "passed", "records": audits,
            "body_sha256": hashlib.sha256(json.dumps(blocks, ensure_ascii=False).encode()).hexdigest()}


def sealed(draft) -> bool:
    value = getattr(draft, "verification", {})
    return (isinstance(value, dict) and value.get("version") == VERSION and value.get("status") == "passed"
            and bool(value.get("records")) and value.get("body_sha256") == seal(draft.blocks, [])["body_sha256"])
