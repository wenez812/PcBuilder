"""Модуль FPS: ТОЛЬКО реальные замеры из таблицы бенчмарков, с источником и условиями теста.

Результаты разных источников, версий игр и конфигураций никогда не усредняются и не смешиваются.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Component, FpsBenchmark


async def benchmark_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(FpsBenchmark)) or 0)


async def available_games(session: AsyncSession) -> list[str]:
    rows = await session.execute(select(FpsBenchmark.game).distinct().order_by(FpsBenchmark.game))
    return [r[0] for r in rows.all()]


async def available_resolutions(session: AsyncSession, game: str) -> list[str]:
    rows = await session.execute(
        select(FpsBenchmark.resolution).where(FpsBenchmark.game == game).distinct().order_by(FpsBenchmark.resolution))
    return [r[0] for r in rows.all()]


async def available_presets(session: AsyncSession, game: str, resolution: str) -> list[str]:
    rows = await session.execute(
        select(FpsBenchmark.preset).where(FpsBenchmark.game == game, FpsBenchmark.resolution == resolution)
        .distinct().order_by(FpsBenchmark.preset))
    return [r[0] for r in rows.all()]


async def find_benchmarks(session: AsyncSession, cpu_id: int, gpu_id: int, game: str,
                          resolution: str, preset: str) -> list[FpsBenchmark]:
    result = await session.execute(
        select(FpsBenchmark).where(
            FpsBenchmark.cpu_id == cpu_id, FpsBenchmark.gpu_id == gpu_id, FpsBenchmark.game == game,
            FpsBenchmark.resolution == resolution, FpsBenchmark.preset == preset,
        ).order_by(FpsBenchmark.test_date.desc().nulls_last(), FpsBenchmark.id.desc()))
    return list(result.scalars())


@dataclass(frozen=True, slots=True)
class OtherMeasurement:
    cpu: str
    gpu: str
    avg_fps: int
    source: str


async def other_measurements(session: AsyncSession, game: str, resolution: str,
                             preset: str, limit: int = 5) -> list[OtherMeasurement]:
    """Замеры тех же условий на ДРУГИХ конфигурациях: для ориентира, не для подмены ответа."""
    cpu, gpu = Component.__table__.alias("c"), Component.__table__.alias("g")
    rows = await session.execute(
        select(cpu.c.name, gpu.c.name, FpsBenchmark.avg_fps, FpsBenchmark.source_name)
        .join(cpu, cpu.c.id == FpsBenchmark.cpu_id).join(gpu, gpu.c.id == FpsBenchmark.gpu_id)
        .where(FpsBenchmark.game == game, FpsBenchmark.resolution == resolution, FpsBenchmark.preset == preset)
        .order_by(FpsBenchmark.id.desc()).limit(limit))
    return [OtherMeasurement(*r) for r in rows.all()]


def render_benchmarks(cpu_name: str, gpu_name: str, game: str, resolution: str, preset: str,
                      rows: list[FpsBenchmark], others: list[OtherMeasurement]) -> str:
    head = (f"🎮 <b>{escape(game)}</b>\n"
            f"CPU: {escape(cpu_name)}\nGPU: {escape(gpu_name)}\n"
            f"{escape(resolution)} / {escape(preset)}\n")
    if not rows:
        text = head + ("\n❌ <b>Нет данных.</b> В базе нет замера для этой конфигурации с такими настройками. "
                       "Я не выдумываю FPS и не пересчитываю результаты других конфигураций.")
        if others:
            text += "\n\n📎 <b>Замеры в тех же условиях на других конфигурациях</b> (напрямую не сопоставимы):\n"
            text += "\n".join(f"• {escape(o.cpu)} + {escape(o.gpu)}: {o.avg_fps} FPS ({escape(o.source)})" for o in others)
        return text

    blocks = [head]
    for r in rows:
        low = f"{r.low1_fps}" if r.low1_fps is not None else "недостаточно данных"
        lines = [f"<b>Средний FPS: {r.avg_fps}</b> · 1% Low: {low}",
                 f"📚 Источник: {escape(r.source_name)}"]
        details = []
        if r.ram_desc:
            details.append(f"RAM: {escape(r.ram_desc)}")
        if r.game_version:
            details.append(f"версия игры: {escape(r.game_version)}")
        if r.test_date:
            details.append(f"дата теста: {r.test_date.strftime('%d.%m.%Y')}")
        if details:
            lines.append("🔬 " + ", ".join(details))
        if r.source_url:
            lines.append(f"🔗 {escape(r.source_url)}")
        if r.notes:
            lines.append(f"📝 {escape(r.notes)}")
        blocks.append("\n".join(lines))
    if len(rows) > 1:
        blocks.append("ℹ️ Несколько замеров сделаны в разных условиях — они показаны отдельно и не усреднялись.")
    return "\n\n".join(blocks)


async def find_for_pair(session: AsyncSession, cpu_id: int, gpu_id: int, game: str,
                        resolution: str) -> list[FpsBenchmark]:
    """Все замеры связки CPU+GPU в игре на данном разрешении (любые настройки)."""
    result = await session.execute(
        select(FpsBenchmark).where(
            FpsBenchmark.cpu_id == cpu_id, FpsBenchmark.gpu_id == gpu_id,
            FpsBenchmark.game == game, FpsBenchmark.resolution == resolution,
        ).order_by(FpsBenchmark.avg_fps.desc()))
    return list(result.scalars())
