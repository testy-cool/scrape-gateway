from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from .models import AttemptLedgerEntry, FailureReason, ScrapeRequest

DEFAULT_EVIDENCE_WINDOW_SECONDS = 7 * 86400
_NON_DOMAIN_FAILURE_REASONS = (
    FailureReason.PROVIDER_UNAVAILABLE.value,
    FailureReason.PROXY_ERROR.value,
)


class DomainMemory:
    def __init__(
        self,
        db_path: str | Path = ".scrape-gateway/memory.sqlite",
        *,
        evidence_window_seconds: int = DEFAULT_EVIDENCE_WINDOW_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if evidence_window_seconds <= 0:
            raise ValueError("routing memory evidence window must be positive")
        self.db_path = Path(db_path)
        self.evidence_window_seconds = evidence_window_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Service-mode dependencies may construct the gateway before the ASGI worker
        # thread starts. Individual operations remain synchronous and transactional.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
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

            create table if not exists attempt_ledger (
              id integer primary key autoincrement,
              run_id text not null check (length(run_id) > 0),
              attempt_index integer not null check (attempt_index > 0),
              recorded_at text not null,
              domain text not null check (length(domain) > 0),
              url text not null check (length(url) > 0),
              country text,
              render_js integer not null check (render_js in (0, 1)),
              premium integer not null check (premium in (0, 1)),
              mobile integer not null check (mobile in (0, 1)),
              screenshot integer not null check (screenshot in (0, 1)),
              provider text not null check (length(provider) > 0),
              route text,
              cost_units real not null check (cost_units >= 0),
              cost_provenance text not null check (cost_provenance in ('exact', 'estimated')),
              success integer not null check (success in (0, 1)),
              status_code integer check (status_code between 100 and 599),
              failure_reason text,
              block_type text,
              latency_ms integer check (latency_ms >= 0),
              unique (run_id, attempt_index)
            );
            create index if not exists idx_attempt_ledger_domain_recorded_provider
              on attempt_ledger(domain, recorded_at, provider);
            create index if not exists idx_attempt_ledger_success_recorded
              on attempt_ledger(success, recorded_at);
            create index if not exists idx_attempt_ledger_profile_provider
              on attempt_ledger(
                domain, country, render_js, premium, mobile, screenshot, provider
              );

            create table if not exists gateway_metadata (
              key text primary key,
              value text not null
            );

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

    def source_instance_id(self) -> str:
        """Return the stable random identity for events exported from this database."""

        with self.conn:
            self.conn.execute(
                "insert or ignore into gateway_metadata(key, value) values ('instance_id', ?)",
                (secrets.token_hex(16),),
            )
        row = self.conn.execute(
            "select value from gateway_metadata where key = 'instance_id'"
        ).fetchone()
        value = str(row["value"]) if row else ""
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError("gateway instance_id must be 32 lowercase hexadecimal characters")
        return value

    @staticmethod
    def domain_for_url(url: str) -> str:
        return urlparse(url).netloc.lower().removeprefix("www.")

    @staticmethod
    def _utc_timestamp(value: datetime | None = None) -> str:
        timestamp = value or datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("recorded timestamps must include a timezone")
        return timestamp.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def record_attempt_ledger(
        self,
        run_id: str,
        request: ScrapeRequest,
        entries: Iterable[AttemptLedgerEntry],
        *,
        recorded_at: datetime | None = None,
    ) -> int:
        timestamp = self._utc_timestamp(recorded_at or self._clock())
        domain = self.domain_for_url(request.url)
        rows = [
            (
                run_id,
                attempt_index,
                timestamp,
                domain,
                request.url,
                request.country,
                int(request.render_js),
                int(request.premium),
                int(request.mobile),
                int(request.screenshot),
                entry.provider,
                entry.route,
                float(entry.cost_units),
                entry.cost_provenance,
                int(entry.success),
                entry.status_code,
                entry.failure_reason.value if entry.failure_reason else None,
                entry.block_type,
                entry.latency_ms,
            )
            for attempt_index, entry in enumerate(entries, start=1)
        ]
        if not rows:
            return 0
        with self.conn:
            self.conn.executemany(
                """
                insert into attempt_ledger(
                  run_id, attempt_index, recorded_at, domain, url, country,
                  render_js, premium, mobile, screenshot, provider, route,
                  cost_units, cost_provenance, success, status_code,
                  failure_reason, block_type, latency_ms
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def attempt_cost_summary(
        self,
        *,
        days: int = 30,
        domain: str | None = None,
        as_of: datetime | None = None,
    ) -> list[dict]:
        if days < 0:
            raise ValueError("days must be non-negative")
        window_end = as_of or self._clock()
        if window_end.tzinfo is None or window_end.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        window_end = window_end.astimezone(UTC)
        window_start = window_end - timedelta(days=days)
        params: list[object] = [
            self._utc_timestamp(window_start),
            self._utc_timestamp(window_end),
        ]
        domain_clause = ""
        if domain:
            normalized_domain = (
                self.domain_for_url(domain)
                if "://" in domain
                else domain.lower().removeprefix("www.")
            )
            domain_clause = "and domain = ?"
            params.append(normalized_domain)
        rows = self.conn.execute(
            f"""
            select domain, provider,
                   count(*) as attempt_count,
                   sum(success) as successful_attempt_count,
                   sum(case when success = 0 then 1 else 0 end) as failed_attempt_count,
                   coalesce(
                     sum(case when success = 1 then cost_units end), 0.0
                   ) as successful_attempt_cost_units,
                   coalesce(
                     sum(case when success = 0 then cost_units end), 0.0
                   ) as failed_attempt_cost_units,
                   sum(cost_units) as total_cost_units
            from attempt_ledger
            where recorded_at >= ? and recorded_at <= ?
              {domain_clause}
            group by domain, provider
            order by domain, provider
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

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

    @staticmethod
    def _request(value: ScrapeRequest | str) -> ScrapeRequest:
        return value if isinstance(value, ScrapeRequest) else ScrapeRequest(value)

    def _evidence_bounds(self, as_of: datetime | None) -> tuple[str, str]:
        window_end = as_of or self._clock()
        if window_end.tzinfo is None or window_end.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        window_end = window_end.astimezone(UTC)
        window_start = window_end - timedelta(seconds=self.evidence_window_seconds)
        return self._utc_timestamp(window_start), self._utc_timestamp(window_end)

    def _profile_params(
        self,
        request: ScrapeRequest,
        *,
        as_of: datetime | None,
    ) -> tuple[object, ...]:
        window_start, window_end = self._evidence_bounds(as_of)
        return (
            self.domain_for_url(request.url),
            window_start,
            window_end,
            request.country,
            int(request.render_js),
            int(request.premium),
            int(request.mobile),
            int(request.screenshot),
            *_NON_DOMAIN_FAILURE_REASONS,
        )

    def provider_cost_effectiveness(
        self,
        request: ScrapeRequest | str,
        *,
        as_of: datetime | None = None,
    ) -> list[dict]:
        """Return recent exact-profile spend normalized by successful outcomes.

        Exact provider-reported costs receive full weight. Estimated costs and their
        outcomes receive half weight so guesses influence the route without competing
        equally with provider-reported billing evidence.
        """

        request = self._request(request)
        rows = self.conn.execute(
            """
            with evidence as (
              select *
              from attempt_ledger
              where domain = ?
                and recorded_at >= ? and recorded_at <= ?
                and country is ? collate nocase
                and render_js = ?
                and premium = ?
                and mobile = ?
                and screenshot = ?
                and (
                  success = 1
                  or failure_reason is null
                  or failure_reason not in (?, ?)
                )
            )
            select provider,
                   count(*) as attempt_count,
                   sum(success) as successful_attempt_count,
                   sum(case when cost_provenance = 'exact' then 1 else 0 end)
                     as exact_attempt_count,
                   sum(case when cost_provenance = 'estimated' then 1 else 0 end)
                     as estimated_attempt_count,
                   sum(case when cost_provenance = 'exact' then 1.0 else 0.5 end)
                     as weighted_attempt_count,
                   sum(
                     cost_units
                     * case when cost_provenance = 'exact' then 1.0 else 0.5 end
                   ) as weighted_cost_units,
                   sum(
                     success
                     * case when cost_provenance = 'exact' then 1.0 else 0.5 end
                   ) as weighted_success_count,
                   max(recorded_at) as last_attempt_at,
                   (
                     select route
                     from evidence as latest_success
                     where latest_success.provider = evidence.provider
                       and latest_success.success = 1
                     order by recorded_at desc, id desc
                     limit 1
                   ) as last_success_tier
            from evidence
            group by provider
            order by provider
            """,
            self._profile_params(request, as_of=as_of),
        ).fetchall()
        scores = []
        for row in rows:
            score = dict(row)
            weighted_successes = float(score["weighted_success_count"] or 0)
            score["cost_per_success"] = (
                float(score["weighted_cost_units"]) / weighted_successes
                if weighted_successes
                else None
            )
            scores.append(score)
        return scores

    def provider_due_for_cost_exploration(
        self,
        request: ScrapeRequest | str,
        provider: str,
        *,
        interval_seconds: int,
        as_of: datetime | None = None,
    ) -> bool:
        """Return whether a provider lacks usable evidence inside the probe interval."""

        if interval_seconds <= 0:
            raise ValueError("cost exploration interval must be positive")
        request = self._request(request)
        window_end = as_of or self._clock()
        if window_end.tzinfo is None or window_end.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        window_end = window_end.astimezone(UTC)
        probe_start = window_end - timedelta(seconds=interval_seconds)
        row = self.conn.execute(
            """
            select 1
            from attempt_ledger
            where domain = ?
              and recorded_at >= ? and recorded_at <= ?
              and country is ? collate nocase
              and render_js = ?
              and premium = ?
              and mobile = ?
              and screenshot = ?
              and (
                success = 1
                or failure_reason is null
                or failure_reason not in (?, ?)
              )
              and provider = ?
            limit 1
            """,
            (
                self.domain_for_url(request.url),
                self._utc_timestamp(probe_start),
                self._utc_timestamp(window_end),
                request.country,
                int(request.render_js),
                int(request.premium),
                int(request.mobile),
                int(request.screenshot),
                *_NON_DOMAIN_FAILURE_REASONS,
                provider,
            ),
        ).fetchone()
        return row is None

    def preferred_provider(
        self,
        request: ScrapeRequest | str,
        *,
        as_of: datetime | None = None,
    ) -> tuple[str, str | None] | None:
        request = self._request(request)
        row = self.conn.execute(
            """
            with evidence as (
              select *
              from attempt_ledger
              where domain = ?
                and recorded_at >= ? and recorded_at <= ?
                and country is ? collate nocase
                and render_js = ?
                and premium = ?
                and mobile = ?
                and screenshot = ?
                and (
                  success = 1
                  or failure_reason is null
                  or failure_reason not in (?, ?)
                )
            ),
            scores as (
              select provider,
                     sum(
                       case
                         when success = 1 then 1
                         when block_type is not null then -3
                         else -1
                       end
                     ) as score,
                     sum(success) as success_count,
                     max(case when success = 1 then recorded_at end) as last_success_at
              from evidence
              group by provider
            )
            select scores.provider,
                   (
                     select route
                     from evidence
                     where evidence.provider = scores.provider and evidence.success = 1
                     order by recorded_at desc, id desc
                     limit 1
                   ) as last_success_tier
            from scores
            where success_count > 0
            order by score desc, last_success_at desc, scores.provider
            limit 1
            """,
            self._profile_params(request, as_of=as_of),
        ).fetchone()
        if not row:
            return None
        return (row["provider"], row["last_success_tier"])

    def providers_due_for_probe(
        self,
        request: ScrapeRequest | str,
        *,
        as_of: datetime | None = None,
    ) -> list[str]:
        request = self._request(request)
        window_start, window_end = self._evidence_bounds(as_of)
        rows = self.conn.execute(
            """
            with ranked as (
              select provider, success, recorded_at,
                     row_number() over (
                       partition by provider
                       order by recorded_at desc, id desc
                     ) as attempt_rank
              from attempt_ledger
              where domain = ?
                and recorded_at <= ?
                and country is ? collate nocase
                and render_js = ?
                and premium = ?
                and mobile = ?
                and screenshot = ?
                and (
                  success = 1
                  or failure_reason is null
                  or failure_reason not in (?, ?)
                )
            ),
            failure_tails as (
              select provider,
                     count(*) as evidence_count,
                     sum(success) as success_count,
                     max(recorded_at) as last_recorded_at
              from ranked
              where attempt_rank <= 5
              group by provider
            )
            select provider
            from failure_tails
            where evidence_count = 5
              and success_count = 0
              and last_recorded_at < ?
            order by last_recorded_at, provider
            """,
            (
                self.domain_for_url(request.url),
                window_end,
                request.country,
                int(request.render_js),
                int(request.premium),
                int(request.mobile),
                int(request.screenshot),
                *_NON_DOMAIN_FAILURE_REASONS,
                window_start,
            ),
        ).fetchall()
        return [row["provider"] for row in rows]

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

    def should_skip_provider(
        self,
        request: ScrapeRequest | str,
        provider: str,
        *,
        as_of: datetime | None = None,
    ) -> bool:
        request = self._request(request)
        row = self.conn.execute(
            """
            select coalesce(sum(success), 0) as success_count,
                   coalesce(
                     sum(case when success = 0 and block_type is null then 1 else 0 end),
                     0
                   ) as failure_count,
                   coalesce(
                     sum(case when success = 0 and block_type is not null then 1 else 0 end),
                     0
                   ) as block_count
            from attempt_ledger
            where domain = ?
              and recorded_at >= ? and recorded_at <= ?
              and country is ? collate nocase
              and render_js = ?
              and premium = ?
              and mobile = ?
              and screenshot = ?
              and (
                success = 1
                or failure_reason is null
                or failure_reason not in (?, ?)
              )
              and provider = ?
            """,
            (*self._profile_params(request, as_of=as_of), provider),
        ).fetchone()
        total_failures = row["failure_count"] + row["block_count"]
        if row["success_count"] == 0 and total_failures >= 5:
            return True
        if total_failures >= 10 and row["success_count"] / max(total_failures, 1) < 0.2:
            return True
        # Kept as a guard so the three skip conditions read as a parallel list. SIM103
        # would collapse it into one long boolean expression alongside a call.
        if (  # noqa: SIM103
            row["success_count"] == 0
            and total_failures > 0
            and self._failed_probe_reclosed(request, provider, as_of=as_of)
        ):
            return True
        return False

    def _failed_probe_reclosed(
        self,
        request: ScrapeRequest,
        provider: str,
        *,
        as_of: datetime | None,
    ) -> bool:
        _, window_end = self._evidence_bounds(as_of)
        rows = self.conn.execute(
            """
            select recorded_at, success
            from attempt_ledger
            where domain = ?
              and recorded_at <= ?
              and country is ? collate nocase
              and render_js = ?
              and premium = ?
              and mobile = ?
              and screenshot = ?
              and (
                success = 1
                or failure_reason is null
                or failure_reason not in (?, ?)
              )
              and provider = ?
            order by recorded_at desc, id desc
            limit 6
            """,
            (
                self.domain_for_url(request.url),
                window_end,
                request.country,
                int(request.render_js),
                int(request.premium),
                int(request.mobile),
                int(request.screenshot),
                *_NON_DOMAIN_FAILURE_REASONS,
                provider,
            ),
        ).fetchall()
        if len(rows) != 6 or rows[0]["success"] or any(row["success"] for row in rows[1:]):
            return False
        latest = datetime.fromisoformat(rows[0]["recorded_at"])
        prior_latest = datetime.fromisoformat(rows[1]["recorded_at"])
        return prior_latest < latest - timedelta(seconds=self.evidence_window_seconds)

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
        meta = {
            m.get("name", m.get("property", "")): m.get("content", "")
            for m in soup.find_all("meta")
            if m.get("content")
        }

        tag_counts: dict[str, int] = {}
        for tag in soup.find_all(True):
            tag_counts[tag.name] = tag_counts.get(tag.name, 0) + 1

        headings = []
        for level in range(1, 4):
            for h in soup.find_all(f"h{level}"):
                headings.append(h.get_text(strip=True)[:80])

        text = soup.get_text(" ", strip=True)
        prices = re.findall(
            r"(?:[$€£¥₹]\s?\d[\d,. ]*|\d[\d,. ]*\s?(?:USD|EUR|GBP|RON|lei))", text, re.IGNORECASE
        )

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
        for key in (
            "link_count",
            "image_count",
            "script_count",
            "form_count",
            "price_count",
            "heading_count",
            "text_length",
        ):
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
