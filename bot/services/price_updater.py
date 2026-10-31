"""Обновление цен из подключённых источников."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bot.database import Database
from bot.integrations import NotConnectedError, PriceSource, SourceError, SourceStatus
from bot.services import catalog_service
from bot.services.importers import upsert_offer

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RefreshReport:
    created: int = 0
    updated: int = 0
    unmatched: int = 0
    errors: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    sources_skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.sources_used and not self.errors:
            return ("⚠️ Нет подключённых источников цен. Загрузите CSV в админ-панели "
                    "или настройте PRICE_FEED_URL.")
        lines = [f"✅ Новых предложений: {self.created}", f"🔄 Обновлено: {self.updated}",
                 f"❓ Нет в каталоге: {self.unmatched}"]
        if self.sources_used:
            lines.append("Источники: " + ", ".join(self.sources_used))
        lines.extend(f"⚠️ {e}" for e in self.errors[:5])
        return "\n".join(lines)


async def refresh_prices(db: Database, sources: list[PriceSource]) -> RefreshReport:
    report = RefreshReport()
    for source in sources:
        if source.status != SourceStatus.CONNECTED:
            report.sources_skipped.append(source.title)
            continue
        try:
            records = await source.fetch()
        except NotConnectedError:
            report.sources_skipped.append(source.title)
            continue
        except SourceError as exc:
            logger.warning("Источник %s: %s", source.title, exc)
            report.errors.append(f"{source.title}: {exc}")
            continue
        report.sources_used.append(source.title)
        async with db.session_factory() as session:
            for rec in records:
                component = await catalog_service.resolve_component(session, rec.category, rec.name)
                if component is None:
                    report.unmatched += 1
                    continue
                created = await upsert_offer(session, component.id, rec.shop, rec.price, rec.in_stock, rec.url)
                report.created += int(created)
                report.updated += int(not created)
            await session.commit()
    return report
