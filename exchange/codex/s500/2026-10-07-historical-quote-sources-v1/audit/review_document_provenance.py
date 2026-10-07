"""Independently validate captured official-document provenance and quotations.

This checks actual retained evidence, not product access. Raw bodies remain private.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(text):
    return re.sub(r"\s+", " ", text).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", required=True)
    args = parser.parse_args()
    stage = Path(args.stage_dir)
    dirs = ["vendor-a/databento", "vendor-a/massive", "vendor-b/theta", "vendor-b/cboe"]
    results = []
    binding = {}
    for rel in dirs:
        root = stage / rel
        raw_manifest = json.loads((root / "manifest.json").read_text())
        entries = raw_manifest if isinstance(raw_manifest, list) else raw_manifest.get("requests", raw_manifest.get("sources"))
        assert isinstance(entries, list)
        index = {entry["id"]: entry for entry in entries}
        assert len(index) == len(entries)
        texts = {}
        statuses = Counter()
        for entry in entries:
            ident = entry["id"]
            raw_rel = entry.get("private_raw_relative_path", entry.get("raw_body_artifact", entry.get("raw_path", f"private/{ident}.body")))
            raw = root / raw_rel
            expected = entry.get("body_sha256", entry.get("sha256"))
            assert digest(raw) == expected, (rel, ident, "body hash mismatch")
            size = entry.get("body_bytes", entry.get("response_bytes"))
            assert raw.stat().st_size == size, (rel, ident, "body size mismatch")
            statuses[str(entry.get("http_status", entry.get("status")))] += 1
            # Authors retained readable extraction beside the body, except Databento.
            candidates = [raw, raw.with_suffix(".txt"), raw.with_suffix(".main.txt"), root / "private" / f"{ident}.txt"]
            texts[ident] = "\n".join(norm(path.read_text(errors="replace")) for path in candidates if path.exists())
        findings_path = root / "findings.json"
        findings = json.loads(findings_path.read_text())
        if rel.endswith("databento") or rel.endswith("cboe"):
            evidence = [item for claim in findings["claims"] for item in claim["evidence"]]
        elif rel.endswith("massive"):
            evidence = json.loads((root / "evidence.json").read_text())["evidence"]
            binding[f"{rel}/evidence.json"] = digest(root / "evidence.json")
        else:
            evidence = findings["evidence"]
        quotation_count = 0
        for item in evidence:
            ident = item.get("document_id", item.get("source_id"))
            entry = index[ident]
            expected = entry.get("body_sha256", entry.get("sha256"))
            assert item["body_sha256"] == expected, (rel, ident, "evidence binding")
            quotes = item.get("exact_quotations", [item.get("quote", item.get("exact_quote_whitespace_normalized"))])
            for quote in quotes:
                assert quote and norm(quote) in texts[ident], (rel, ident, "quotation missing", quote)
                quotation_count += 1
        for filename in ["manifest.json", "findings.json", "findings.md"]:
            binding[f"{rel}/{filename}"] = digest(root / filename)
        results.append({"vendor_directory": rel, "captured_documents_hash_and_size_verified": len(entries), "http_status_counts": dict(statuses), "evidence_entries_verified": len(evidence), "exact_quotations_verified": quotation_count})
    result = {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "status": "document_provenance_and_quoted_passages_verified",
        "vendors": results,
        "total_captured_documents": sum(row["captured_documents_hash_and_size_verified"] for row in results),
        "total_evidence_entries": sum(row["evidence_entries_verified"] for row in results),
        "total_exact_quotations": sum(row["exact_quotations_verified"] for row in results),
        "scope": "Local source-body hash/size and exact quoted passage reconciliation. Does not authenticate access, establish license, validate data coverage or certify quote executability.",
        "historical_quotes_acquired_by_audit": 0,
        "sha256": binding,
    }
    (stage / "audit" / "document-provenance-review.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ["status", "vendors", "total_captured_documents", "total_evidence_entries", "total_exact_quotations"]}))


if __name__ == "__main__":
    main()
