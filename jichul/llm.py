"""Claude API 호출. 구조화 출력은 tool_use 강제로 받고 pydantic 으로 검증한다."""
from __future__ import annotations

import base64
import os
from pathlib import Path

from pydantic import ValidationError

from .models import DraftResult, ExtractionResult, Receipt, ReceiptRaw
from .prompts import EXTRACT_SYSTEM, draft_system, draft_user

DEFAULT_MODEL = os.environ.get("JICHUL_MODEL", "claude-sonnet-5-5")
MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}


def make_client():
    import anthropic
    return anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수


def inline_refs(schema: dict) -> dict:
    """pydantic 이 만든 $defs/$ref 를 풀어 단일 스키마로 만든다(도구 input_schema 호환용)."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    return walk(schema)


def _tool(name: str, desc: str, model_cls) -> dict:
    return {"name": name, "description": desc, "input_schema": inline_refs(model_cls.model_json_schema())}


def _file_block(path: Path) -> dict:
    ext = path.suffix.lower()
    data = base64.standard_b64encode(path.read_bytes()).decode()
    if ext == ".pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
    if ext in MEDIA:
        return {"type": "image", "source": {"type": "base64", "media_type": MEDIA[ext], "data": data}}
    raise ValueError(f"지원하지 않는 파일 형식: {path.name} (jpg/png/webp/gif/pdf 만 가능. HEIC 는 JPG 로 변환하세요)")


def _tool_input(resp, name: str) -> dict:
    for b in resp.content:
        if getattr(b, "type", None) == "tool_use" and b.name == name:
            return b.input
    raise RuntimeError(f"모델이 {name} 도구를 호출하지 않았습니다 (stop_reason={getattr(resp, 'stop_reason', '?')})")


def extract_file(client, path: Path, model: str = DEFAULT_MODEL) -> list[ReceiptRaw]:
    resp = client.messages.create(
        model=model, max_tokens=4096, system=EXTRACT_SYSTEM,
        tools=[_tool("submit_receipts", "파일에서 판독한 영수증 목록을 제출", ExtractionResult)],
        tool_choice={"type": "tool", "name": "submit_receipts"},
        messages=[{"role": "user", "content": [_file_block(path), {"type": "text", "text": f"파일명: {path.name}\n이 파일의 영수증을 모두 판독하세요."}]}],
    )
    return ExtractionResult(**_tool_input(resp, "submit_receipts")).receipts


def extract_dir(client, folder: Path, model: str = DEFAULT_MODEL) -> list[Receipt]:
    out: list[Receipt] = []
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {*MEDIA, ".pdf"})
    for f in files:
        for raw in extract_file(client, f, model):
            out.append(Receipt(id=f"r{len(out) + 1}", source_file=f.name, **raw.model_dump()))
    return out


def draft_expenses(client, meta: dict, receipts: list[Receipt], accounts_cfg: dict, rules: dict,
                   examples: list[dict], model: str = DEFAULT_MODEL, retries: int = 1) -> DraftResult:
    system = draft_system(accounts_cfg, rules, examples)
    user = draft_user(meta, [r.model_dump() for r in receipts])
    messages = [{"role": "user", "content": user}]
    tool = _tool("submit_draft", "목별 지출결의서 초안을 제출", DraftResult)
    last_err = None
    for _ in range(retries + 1):
        resp = client.messages.create(model=model, max_tokens=8000, system=system, tools=[tool],
                                      tool_choice={"type": "tool", "name": "submit_draft"}, messages=messages)
        try:
            return DraftResult(**_tool_input(resp, "submit_draft"))
        except (ValidationError, RuntimeError) as e:
            last_err = e
            messages = [{"role": "user", "content": user + f"\n\n[이전 응답의 형식 오류 — 고쳐서 다시 제출]\n{e}"}]
    raise RuntimeError(f"초안 생성 실패: {last_err}")
