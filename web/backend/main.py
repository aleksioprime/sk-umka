"""API мониторинга сети UMKA: устройства, их состояние и события."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import and_, func, select, text, tuple_
from sqlalchemy.orm import aliased

from auth import current_user, router as auth_router
from shared.db import Device, Event, Session, create_tables


@asynccontextmanager
async def lifespan(_app):
    # Таблицы пользователей нужны API сразу, даже если worker ещё не запускался.
    await create_tables()
    yield


app = FastAPI(title="UMKA monitoring", lifespan=lifespan)
app.include_router(auth_router)
# Данные устройств - только для вошедших пользователей; /api/health открыт для Coolify.
protected = [Depends(current_user)]
ONLINE_WINDOW = timedelta(minutes=3)  # Статус приходит раз в минуту.


def device_view(device: Device) -> dict:
    online = bool(device.last_seen and datetime.now(timezone.utc) - device.last_seen < ONLINE_WINDOW)
    return {"id": device.id, "name": device.name, "online": online,
            "lastSeen": device.last_seen, "status": device.status}


@app.get("/api/health")
async def health() -> dict:
    """Проверка для Docker и Coolify: API отвечает и база доступна."""
    async with Session() as session:
        await session.execute(select(1))
    return {"ok": True}


@app.get("/api/devices", dependencies=protected)
async def devices() -> list[dict]:
    async with Session() as session:
        result = await session.scalars(select(Device).order_by(Device.id))
        return [device_view(device) for device in result]


@app.get("/api/devices/{device_id}", dependencies=protected)
async def device(device_id: str) -> dict:
    async with Session() as session:
        item = await session.get(Device, device_id)
        if item is None:
            raise HTTPException(404, "Устройство не найдено")
        return device_view(item)


@app.get("/api/devices/{device_id}/events", dependencies=protected)
async def events(device_id: str, limit: int = 100) -> list[dict]:
    async with Session() as session:
        result = await session.scalars(
            select(Event).where(Event.device_id == device_id)
            .order_by(Event.timestamp.desc()).limit(min(limit, 1000)))
        return [{"eventId": e.event_id, "kind": e.kind, "timestamp": e.timestamp, **e.payload} for e in result]


@app.get("/api/summary", dependencies=protected)
async def summary() -> dict:
    """Сводка по сети: устройства и сортировки по типам отходов."""
    async with Session() as session:
        rows = await session.execute(
            # Группировка по номеру столбца: ключ JSON в SELECT и GROUP BY иначе
            # передаётся разными параметрами, и PostgreSQL не считает их одним выражением.
            select(Event.payload["waste_type"].as_string(), func.count())
            .where(Event.kind == "sort").group_by(text("1")))
        devices_total = await session.scalar(select(func.count()).select_from(Device))
        return {"devices": devices_total, "sortedByType": {kind or "unknown": count for kind, count in rows}}


@app.get("/api/devices/{device_id}/sorts", dependencies=protected)
async def device_sorts(
    device_id: str,
    days: int = Query(0, ge=0, le=365),
    waste_type: str | None = None,
    answer: Literal["yes", "no", "none"] | None = None,
    before: int | None = Query(None, gt=0),
    limit: int = Query(25, ge=1, le=100),
) -> dict:
    """Журнал завершённых сортировок с ответами и фильтрами по устройству."""
    feedback = aliased(Event)
    response = feedback.payload["answer"].as_string()
    query = (select(Event, response)
             .outerjoin(feedback, and_(feedback.device_id == Event.device_id,
                                       feedback.event_id == Event.event_id,
                                       feedback.kind == "feedback"))
             .where(Event.device_id == device_id, Event.kind == "sort"))
    if days:
        query = query.where(Event.timestamp >= datetime.now(timezone.utc) - timedelta(days=days))
    if waste_type:
        query = query.where(Event.payload["waste_type"].as_string() == waste_type)
    if answer:
        query = query.where(response.is_(None) if answer == "none" else response == answer)

    async with Session() as session:
        if await session.get(Device, device_id) is None:
            raise HTTPException(404, "Устройство не найдено")
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        if before:
            cursor = await session.scalar(select(Event).where(
                Event.id == before, Event.device_id == device_id, Event.kind == "sort"))
            if cursor is None:
                raise HTTPException(400, "Страница журнала недоступна")
            query = query.where(tuple_(Event.timestamp, Event.id) < tuple_(cursor.timestamp, cursor.id))
        rows = (await session.execute(
            query.order_by(Event.timestamp.desc(), Event.id.desc()).limit(limit + 1)
        )).all()
    return {
        "total": total,
        "next": rows[limit - 1][0].id if len(rows) > limit else None,
        "items": [
            {**event.payload, "timestamp": event.timestamp, "answer": reply}
            for event, reply in rows[:limit]
        ],
    }
