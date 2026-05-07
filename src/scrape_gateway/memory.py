from __future__ import annotations

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
