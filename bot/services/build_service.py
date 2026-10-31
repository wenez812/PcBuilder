"""Сохранённые сборки: создание, редактирование, удаление, шеринг, сравнение, история подборов."""
from __future__ import annotations

import re
import secrets

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Build, BuildItem, SelectionHistory, User
from bot.models.base import utcnow
from bot.services import catalog_service
from bot.services.parts import CATEGORIES, CATEGORY_SHORT, Part

MAX_BUILDS_PER_USER = 30
MAX_NAME_LENGTH = 64
HISTORY_LIMIT = 10
_NAME_RE = re.compile(r"[\x00-\x1f\x7f]")


class BuildError(ValueError):
    """Ошибка бизнес-логики со сообщением, понятным пользователю."""


def validate_build_name(raw: str | None) -> str:
    name = _NAME_RE.sub(" ", raw or "").strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        raise BuildError("Название не может быть пустым.")
    if len(name) > MAX_NAME_LENGTH:
        raise BuildError(f"Название слишком длинное: максимум {MAX_NAME_LENGTH} символов.")
    return name


def build_total(parts: dict[str, Part]) -> tuple[int, int]:
    """(сумма известных цен, число деталей без актуальной цены)."""
    known = [p.price for p in parts.values() if p.price is not None]
    return sum(known), len(parts) - len(known)


async def _new_public_id(session: AsyncSession) -> str:
    for _ in range(10):
        candidate = secrets.token_hex(4)  # 8 hex-символов, подходит для deep-link /start
        exists = await session.scalar(select(func.count()).select_from(Build).where(Build.public_id == candidate))
        if not exists:
            return candidate
    raise RuntimeError("Не удалось сгенерировать уникальный ID сборки")


async def count_builds(session: AsyncSession, user: User) -> int:
    return int(await session.scalar(select(func.count()).select_from(Build).where(Build.user_id == user.id)) or 0)


async def create_build(
    session: AsyncSession, user: User, name: str, parts: dict[str, Part], params: dict | None = None
) -> Build:
    name = validate_build_name(name)
    if not parts:
        raise BuildError("В сборке нет ни одной детали.")
    if await count_builds(session, user) >= MAX_BUILDS_PER_USER:
        raise BuildError(f"Достигнут лимит: {MAX_BUILDS_PER_USER} сохранённых сборок. Удалите ненужные.")
    build = Build(public_id=await _new_public_id(session), user_id=user.id, name=name, params=params,
                  created_at=utcnow(), updated_at=utcnow())
    session.add(build)
    await session.flush()
    for category, part in parts.items():
        if category not in CATEGORIES:
            raise BuildError(f"Неизвестная категория: {category}")
        session.add(BuildItem(build_id=build.id, category=category, component_id=part.id,
                              price_at_save=part.price))
    await session.flush()
    return build


async def list_builds(session: AsyncSession, user: User) -> list[Build]:
    result = await session.execute(
        select(Build).where(Build.user_id == user.id).order_by(Build.updated_at.desc(), Build.id.desc())
    )
    return list(result.scalars())


async def get_user_build(session: AsyncSession, user: User, build_id: int) -> Build | None:
    """Сборка только если она принадлежит пользователю."""
    build = await session.get(Build, build_id)
    return build if build is not None and build.user_id == user.id else None


async def get_shared_build(session: AsyncSession, public_id: str) -> Build | None:
    if not re.fullmatch(r"[0-9a-f]{8}", public_id or ""):
        return None
    result = await session.execute(select(Build).where(Build.public_id == public_id, Build.is_shared.is_(True)))
    return result.scalar_one_or_none()


async def load_parts(session: AsyncSession, build: Build) -> dict[str, Part]:
    items = (await session.execute(select(BuildItem).where(BuildItem.build_id == build.id))).scalars().all()
    parts = await catalog_service.get_parts(session, [i.component_id for i in items])
    return {i.category: parts[i.component_id] for i in items if i.component_id in parts}


async def rename_build(session: AsyncSession, build: Build, raw_name: str) -> None:
    build.name = validate_build_name(raw_name)
    build.updated_at = utcnow()
    await session.flush()


async def delete_build(session: AsyncSession, build: Build) -> None:
    await session.execute(delete(BuildItem).where(BuildItem.build_id == build.id))
    await session.execute(delete(Build).where(Build.id == build.id))
    await session.flush()


async def replace_item(session: AsyncSession, build: Build, category: str, part: Part) -> None:
    if category not in CATEGORIES or part.category != category:
        raise BuildError("Деталь не подходит для этой категории.")
    item = (await session.execute(
        select(BuildItem).where(BuildItem.build_id == build.id, BuildItem.category == category)
    )).scalar_one_or_none()
    if item is None:
        session.add(BuildItem(build_id=build.id, category=category, component_id=part.id, price_at_save=part.price))
    else:
        item.component_id, item.price_at_save = part.id, part.price
    build.updated_at = utcnow()
    await session.flush()


async def remove_item(session: AsyncSession, build: Build, category: str) -> None:
    await session.execute(delete(BuildItem).where(BuildItem.build_id == build.id, BuildItem.category == category))
    build.updated_at = utcnow()
    await session.flush()


async def set_shared(session: AsyncSession, build: Build, shared: bool) -> None:
    build.is_shared = shared
    await session.flush()


async def copy_build(session: AsyncSession, source: Build, new_owner: User) -> Build:
    parts = await load_parts(session, source)
    return await create_build(session, new_owner, f"{source.name} (копия)"[:MAX_NAME_LENGTH], parts, source.params)


def share_link(bot_username: str, build: Build) -> str:
    return f"https://t.me/{bot_username}?start=b_{build.public_id}"


# ---------------------------------------------------------------- сравнение

def compare_builds(a: dict[str, Part], b: dict[str, Part]) -> list[dict]:
    """Построчное сравнение: для каждой категории — детали и разница цен (если обе известны)."""
    rows = []
    for category in CATEGORIES:
        pa, pb = a.get(category), b.get(category)
        if pa is None and pb is None:
            continue
        diff = None
        if pa is not None and pb is not None and pa.price is not None and pb.price is not None:
            diff = pb.price - pa.price
        rows.append({"category": category, "title": CATEGORY_SHORT[category], "a": pa, "b": pb,
                     "same": pa is not None and pb is not None and pa.id == pb.id, "price_diff": diff})
    return rows


# ---------------------------------------------------------------- история подборов

async def add_history(
    session: AsyncSession, user: User, kind: str, summary: str, params: dict | None, total: int | None
) -> None:
    session.add(SelectionHistory(user_id=user.id, kind=kind, summary=summary[:2000], params=params, total_rub=total))
    await session.flush()
    # Храним ограниченную историю
    old_ids = (await session.execute(
        select(SelectionHistory.id).where(SelectionHistory.user_id == user.id)
        .order_by(SelectionHistory.id.desc()).offset(50)
    )).scalars().all()
    if old_ids:
        await session.execute(delete(SelectionHistory).where(SelectionHistory.id.in_(old_ids)))


async def list_history(session: AsyncSession, user: User, limit: int = HISTORY_LIMIT) -> list[SelectionHistory]:
    result = await session.execute(
        select(SelectionHistory).where(SelectionHistory.user_id == user.id)
        .order_by(SelectionHistory.id.desc()).limit(limit)
    )
    return list(result.scalars())
