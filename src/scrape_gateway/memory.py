from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse


class DomainMemory:
    def __init__(self, db_path: str | Path = ".scrape-gateway/memory.sqlite") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            create table if not exists domain_provider_stats (
              domain text not null,
              provider text not null,
              success_count integer default 0,
              failure_count integer default 0,
              block_count integer default 0,
              last_success_country text,
              last_success_tier text,
              last_block_type text,
              updated_at datetime default current_timestamp,
              primary key (domain, provider)
            );

            create table if not exists page_history (
              id integer primary key autoincrement,
              url text not null,
              content_hash text not null,
              fingerprint text not null,
              changes text,
              provider text,
              scraped_at datetime default current_timestamp
            );
            create index if not exists idx_page_history_url on page_history(url);

            create table if not exists extraction_patterns (
              domain text primary key,
              selector text not null,
              field_map text not null default '{}',
              learned_at datetime default current_timestamp
            );

            -- legacy table kept for backward compat during migration
            create table if not exists domain_routes (
              domain text primary key,
              provider text not null,
              country text,
              render_js integer default 0,
              premium integer default 0,
              success_count integer default 0,
              failure_count integer default 0,
              updated_at datetime default current_timestamp
            );
            """
        )

    @staticmethod
    def domain_for_url(url: str) -> str:
        return urlparse(url).netloc.lower().removeprefix("www.")

    def remember_success(
        self,
        url: str,
        provider: str,
        country: str | None,
        render_js: bool,
        premium: bool,
        tier: str | None = None,
    ) -> None:
        domain = self.domain_for_url(url)
        self.conn.execute(
            """
            insert into domain_provider_stats(domain, provider, success_count, last_success_country, last_success_tier)
            values (?, ?, 1, ?, ?)
            on conflict(domain, provider) do update set
              success_count = success_count + 1,
              last_success_country = excluded.last_success_country,
              last_success_tier = excluded.last_success_tier,
              updated_at = current_timestamp
            """,
            (domain, provider, country, tier),
        )
        self.conn.execute(
            """
            insert into domain_routes(domain, provider, country, render_js, premium, success_count)
            values (?, ?, ?, ?, ?, 1)
            on conflict(domain) do update set
              provider=excluded.provider,
              country=excluded.country,
              render_js=excluded.render_js,
              premium=excluded.premium,
              success_count=success_count + 1,
              updated_at=current_timestamp
            """,
            (domain, provider, country, int(render_js), int(premium)),
        )
        self.conn.commit()

    def remember_failure(
        self,
        url: str,
        provider: str,
        block_type: str | None = None,
    ) -> None:
        domain = self.domain_for_url(url)
        if block_type:
            self.conn.execute(
                """
                insert into domain_provider_stats(domain, provider, block_count, last_block_type)
                values (?, ?, 1, ?)
                on conflict(domain, provider) do update set
                  block_count = block_count + 1,
                  last_block_type = excluded.last_block_type,
                  updated_at = current_timestamp
                """,
                (domain, provider, block_type),
            )
        else:
            self.conn.execute(
                """
                insert into domain_provider_stats(domain, provider, failure_count)
                values (?, ?, 1)
                on conflict(domain, provider) do update set
                  failure_count = failure_count + 1,
                  updated_at = current_timestamp
                """,
                (domain, provider),
            )
        self.conn.commit()

    def preferred_provider(self, url: str) -> tuple[str, str | None] | None:
        domain = self.domain_for_url(url)
        row = self.conn.execute(
            """
            select provider, last_success_tier from domain_provider_stats
            where domain = ? and success_count > 0
            order by
              success_count - (failure_count + block_count * 3) desc,
              updated_at desc
            limit 1
            """,
            (domain,),
        ).fetchone()
        if not row:
            return None
        return (row["provider"], row["last_success_tier"])

    def provider_stats(self, url: str) -> list[dict]:
        domain = self.domain_for_url(url)
        rows = self.conn.execute(
            """
            select provider, success_count, failure_count, block_count,
                   last_success_country, last_success_tier, last_block_type, updated_at
            from domain_provider_stats
            where domain = ?
            order by success_count - (failure_count + block_count * 3) desc
            """,
            (domain,),
        ).fetchall()
        return [dict(r) for r in rows]

    def should_skip_provider(self, url: str, provider: str) -> bool:
        domain = self.domain_for_url(url)
        row = self.conn.execute(
            """
            select success_count, failure_count, block_count
            from domain_provider_stats
            where domain = ? and provider = ?
            """,
            (domain, provider),
        ).fetchone()
        if not row:
            return False
        total_failures = row["failure_count"] + row["block_count"]
        if row["success_count"] == 0 and total_failures >= 5:
            return True
        if total_failures >= 10 and row["success_count"] / max(total_failures, 1) < 0.2:
            return True
        return False

    # --- Extraction pattern memory ---

    def get_extraction(self, domain: str) -> tuple[str, dict] | None:
        row = self.conn.execute(
            "select selector, field_map from extraction_patterns where domain = ?",
            (domain,),
        ).fetchone()
        if not row:
            return None
        return (row["selector"], json.loads(row["field_map"]))

    def learn_extraction(self, domain: str, selector: str, field_map: dict) -> None:
        self.conn.execute(
            """insert into extraction_patterns(domain, selector, field_map)
               values (?, ?, ?)
               on conflict(domain) do update set
                 selector=excluded.selector,
                 field_map=excluded.field_map,
                 learned_at=current_timestamp""",
            (domain, selector, json.dumps(field_map)),
        )
        self.conn.commit()

    # --- Page history / change detection ---

    @staticmethod
    def fingerprint(html: str) -> dict:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        links = [a["href"] for a in soup.find_all("a", href=True)]
        forms = [f.get("action", "") for f in soup.find_all("form")]
        images = len(soup.find_all("img"))
        scripts = len(soup.find_all("script"))
        meta = {m.get("name", m.get("property", "")): m.get("content", "")
                for m in soup.find_all("meta") if m.get("content")}

        tag_counts: dict[str, int] = {}
        for tag in soup.find_all(True):
            tag_counts[tag.name] = tag_counts.get(tag.name, 0) + 1

        headings = []
        for level in range(1, 4):
            for h in soup.find_all(f"h{level}"):
                headings.append(h.get_text(strip=True)[:80])

        text = soup.get_text(" ", strip=True)
        prices = re.findall(r'(?:[$€£¥₹]\s?\d[\d,. ]*|\d[\d,. ]*\s?(?:USD|EUR|GBP|RON|lei))', text, re.I)

        return {
            "link_count": len(links),
            "image_count": images,
            "script_count": scripts,
            "form_count": len(forms),
            "price_count": len(prices),
            "heading_count": len(headings),
            "headings": headings[:10],
            "tag_counts": dict(sorted(tag_counts.items(), key=lambda x: -x[1])[:15]),
            "title": (soup.title.string.strip() if soup.title and soup.title.string else ""),
            "meta_description": meta.get("description", "")[:200],
            "text_length": len(text),
        }

    @staticmethod
    def _diff_fingerprints(old: dict, new: dict) -> list[str]:
        changes = []
        for key in ("link_count", "image_count", "script_count", "form_count",
                     "price_count", "heading_count", "text_length"):
            ov, nv = old.get(key, 0), new.get(key, 0)
            if ov != nv:
                diff = nv - ov
                sign = "+" if diff > 0 else ""
                changes.append(f"{key}: {ov} → {nv} ({sign}{diff})")
        if old.get("title") != new.get("title"):
            changes.append(f"title: {old.get('title', '')!r} → {new.get('title', '')!r}")
        old_heads = set(old.get("headings", []))
        new_heads = set(new.get("headings", []))
        added = new_heads - old_heads
        removed = old_heads - new_heads
        if added:
            changes.append(f"headings added: {', '.join(list(added)[:3])}")
        if removed:
            changes.append(f"headings removed: {', '.join(list(removed)[:3])}")
        return changes

    def record_scrape(self, url: str, html: str, provider: str | None = None) -> list[str]:
        content_hash = hashlib.sha256(html.encode()).hexdigest()[:16]
        fp = self.fingerprint(html)
        fp_json = json.dumps(fp, ensure_ascii=False)

        last = self.conn.execute(
            "select content_hash, fingerprint from page_history where url = ? order by id desc limit 1",
            (url,),
        ).fetchone()

        changes: list[str] = []
        if last:
            if last["content_hash"] == content_hash:
                changes = ["no changes"]
            else:
                old_fp = json.loads(last["fingerprint"])
                changes = self._diff_fingerprints(old_fp, fp)
                if not changes:
                    changes = ["content changed (hash differs, structure same)"]

        self.conn.execute(
            "insert into page_history(url, content_hash, fingerprint, changes, provider) values (?,?,?,?,?)",
            (url, content_hash, fp_json, json.dumps(changes) if changes else None, provider),
        )
        self.conn.commit()
        return changes

    def get_history(self, url: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """select url, content_hash, fingerprint, changes, provider, scraped_at
               from page_history where url = ? order by id desc limit ?""",
            (url, limit),
        ).fetchall()
        result = []
        for r in rows:
            entry = dict(r)
            entry["fingerprint"] = json.loads(entry["fingerprint"])
            entry["changes"] = json.loads(entry["changes"]) if entry["changes"] else []
            result.append(entry)
        return result
