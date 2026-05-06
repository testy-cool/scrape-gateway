from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlparse


class DomainMemory:
    def __init__(self, db_path: str | Path = ".scrape-gateway/memory.sqlite") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            """
            create table if not exists domain_routes (
              domain text primary key,
              provider text not null,
              country text,
              render_js integer default 0,
              premium integer default 0,
              success_count integer default 0,
              failure_count integer default 0,
              updated_at datetime default current_timestamp
            )
            """
        )

    @staticmethod
    def domain_for_url(url: str) -> str:
        return urlparse(url).netloc.lower().removeprefix("www.")

    def remember_success(
        self, url: str, provider: str, country: str | None, render_js: bool, premium: bool
    ) -> None:
        domain = self.domain_for_url(url)
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

    def preferred_provider(self, url: str) -> str | None:
        domain = self.domain_for_url(url)
        row = self.conn.execute(
            "select provider from domain_routes where domain = ?", (domain,)
        ).fetchone()
        return row[0] if row else None
