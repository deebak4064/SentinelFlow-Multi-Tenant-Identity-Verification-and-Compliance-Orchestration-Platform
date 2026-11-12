from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require
from app.api.errors import not_found
from app.db import get_db
from app.engine.providers import REGISTRY, breaker_for, execute_with_resilience
from app.models import ProviderConfig, ProviderResult
from app.schemas import ProviderConfigIn
from app.security.permissions import Principal
from app.services import audit

router = APIRouter(prefix="/providers", tags=["providers"])

SAMPLE_SUBJECT = {  # synthetic test identity used by the "test connection" action
    "full_name": "Test Subject", "screen_name": "Test Subject", "dob": "1990-01-01", "country": "GB",
    "document_type": "passport", "document_number": "TST123456", "company_name": "Sample Widgets Ltd",
    "registration_number": "SW123456", "email": "test@example.com",
}


async def _config(db, p, key) -> ProviderConfig:
    if key not in REGISTRY:
        raise not_found("provider")
    cfg = await db.scalar(select(ProviderConfig).where(ProviderConfig.org_id == p.org_id, ProviderConfig.provider_key == key))
    if cfg is None:
        cfg = ProviderConfig(org_id=p.org_id, provider_key=key, enabled=True, timeout_ms=800, max_retries=2, simulation={"latency_ms": 40})
        db.add(cfg)
        await db.flush()
    return cfg


def _cfg_dict(c: ProviderConfig) -> dict:
    return {"enabled": c.enabled, "timeout_ms": c.timeout_ms, "max_retries": c.max_retries, "simulation": c.simulation}


@router.get("")
async def list_providers(p: Principal = Depends(require("providers:read")), db: AsyncSession = Depends(get_db)):
    since = datetime.now(UTC) - timedelta(hours=24)
    stats = {r.provider_key: r for r in (await db.execute(
        select(ProviderResult.provider_key, func.count().label("calls"),
               func.sum((ProviderResult.outcome != "error").cast(Integer)).label("ok"),
               func.avg(ProviderResult.latency_ms).label("avg_latency"),
               func.percentile_cont(0.95).within_group(ProviderResult.latency_ms).label("p95"),
               func.sum((ProviderResult.attempts > 1).cast(Integer)).label("retried"),
               func.sum(ProviderResult.cache_hit.cast(Integer)).label("cache_hits"))
        .where(ProviderResult.org_id == p.org_id, ProviderResult.created_at >= since, ProviderResult.outcome != "skipped")
        .group_by(ProviderResult.provider_key))).all()}
    cfgs = {c.provider_key: c for c in (await db.scalars(select(ProviderConfig).where(ProviderConfig.org_id == p.org_id))).all()}
    out = []
    for key, a in REGISTRY.items():
        s, c = stats.get(key), cfgs.get(key)
        out.append({
            "key": key, "display_name": a.display_name, "description": a.description, "check_type": a.check_type,
            "required_fields": list(a.required_fields), "simulated": True,
            "config": _cfg_dict(c) if c else None, "circuit_state": breaker_for(p.org_id, key).state,
            "stats_24h": {"calls": s.calls, "success_rate": round(s.ok / s.calls, 4) if s.calls else None,
                          "avg_latency_ms": round(s.avg_latency or 0, 2), "p95_latency_ms": round(s.p95 or 0, 2),
                          "retried": s.retried, "cache_hits": s.cache_hits} if s else {"calls": 0},
        })
    return out


@router.patch("/{provider_key}")
async def update_provider(provider_key: str, body: ProviderConfigIn, p: Principal = Depends(require("providers:write")), db: AsyncSession = Depends(get_db)):
    cfg = await _config(db, p, provider_key)
    before = _cfg_dict(cfg)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(cfg, k, {**(cfg.simulation or {}), **v} if k == "simulation" else v)
    audit.record(db, "provider.updated", org_id=p.org_id, principal=p, resource_type="provider", resource_id=provider_key,
                 details={"before": before, "after": _cfg_dict(cfg)})
    await db.commit()
    return _cfg_dict(cfg)


@router.post("/{provider_id}/test", summary="Run the provider against a synthetic sample subject")
async def test_provider(provider_id: str, p: Principal = Depends(require("providers:write")), db: AsyncSession = Depends(get_db)):
    cfg = await _config(db, p, provider_id)
    adapter = REGISTRY[provider_id]
    out = await execute_with_resilience(adapter, adapter.build_request(SAMPLE_SUBJECT), _cfg_dict(cfg), breaker_for(p.org_id, provider_id))
    normalized = adapter.normalize(out.raw, SAMPLE_SUBJECT).to_dict() if out.raw else None
    audit.record(db, "provider.tested", org_id=p.org_id, principal=p, resource_type="provider", resource_id=provider_id,
                 details={"ok": out.raw is not None, "error": out.error_code, "attempts": out.attempts})
    await db.commit()
    return {"ok": out.raw is not None, "attempts": out.attempts, "latency_ms": round(out.latency_ms, 2), "error_code": out.error_code,
            "normalized": normalized, "circuit_state": breaker_for(p.org_id, provider_id).state}
