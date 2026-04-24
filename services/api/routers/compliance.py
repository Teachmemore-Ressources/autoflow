"""
Compliance
==========
Endpoints for compliance reporting, CSV export and scoring.

Endpoints
---------
GET  /compliance/export/jobs.csv   – Download AWX job history as RFC-4180 CSV
GET  /compliance/score             – Current compliance score (0-100)
GET  /compliance/report            – On-demand HTML compliance report
GET  /compliance/report/latest     – Last auto-generated report (cached)
POST /compliance/report/generate   – Trigger immediate report generation
"""
from __future__ import annotations

import csv
import io
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from routers.auth import require_auth

logger = logging.getLogger("compliance")

router = APIRouter(prefix="/compliance", tags=["Compliance"])

# Chemin où le dernier rapport HTML est sauvegardé (persistant entre requêtes)
_REPORT_CACHE_FILE = Path(os.getenv("COMPLIANCE_REPORT_PATH", "/tmp/compliance_latest.html"))


# ── Helpers AWX ───────────────────────────────────────────────────────────────

def _human_dur(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = float(seconds)
    if s < 60:
        return f"{s:.0f}s"
    m, sec = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {sec}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


async def _get_jobs(http, page: int = 1, page_size: int = 200, days: int = 30) -> list[dict]:
    """Récupère les jobs AWX des N derniers jours."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qs = f"page={page}&page_size={page_size}&order_by=-id&finished__gte={since}"
    r = await http.get(f"/api/v2/jobs/?{qs}")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail="AWX unreachable")
    data = r.json()
    jobs = data.get("results", [])
    # Récupération multi-pages si nécessaire
    if data.get("next") and page_size < 1000:
        next_jobs = await _get_jobs(http, page=page + 1, page_size=page_size, days=days)
        jobs.extend(next_jobs)
    return jobs


def _enrich_job(j: dict) -> dict:
    sf = j.get("summary_fields", {})
    return {
        "id":              j.get("id"),
        "name":            j.get("name", ""),
        "status":          j.get("status", ""),
        "job_type":        j.get("job_type", ""),
        "started":         j.get("started", ""),
        "finished":        j.get("finished", ""),
        "elapsed_seconds": j.get("elapsed", ""),
        "elapsed_human":   _human_dur(j.get("elapsed")),
        "failed":          j.get("failed", False),
        "job_template":    sf.get("job_template", {}).get("name", ""),
        "launched_by":     sf.get("created_by", {}).get("username", ""),
        "execution_node":  j.get("execution_node", ""),
    }


def _compute_score(jobs: list[dict]) -> dict[str, Any]:
    """
    Calcule un score de conformité (0-100) basé sur les résultats des jobs AWX.

    Score = (jobs réussis / jobs terminés) × 100
    Pénalité de -5 par tranche de 10 % d'erreurs.
    """
    terminal_statuses = {"successful", "failed", "error", "canceled"}
    terminal = [j for j in jobs if j.get("status") in terminal_statuses]
    successful = [j for j in terminal if j.get("status") == "successful"]
    failed     = [j for j in terminal if j.get("status") in ("failed", "error")]
    canceled   = [j for j in terminal if j.get("status") == "canceled"]

    total = len(terminal)
    if total == 0:
        return {
            "score":              100,
            "grade":              "A",
            "total_jobs":         0,
            "successful":         0,
            "failed":             0,
            "canceled":           0,
            "success_rate":       100.0,
            "failure_rate":       0.0,
        }

    success_rate = round(len(successful) / total * 100, 1)
    failure_rate = round(len(failed)     / total * 100, 1)
    score        = max(0, min(100, round(success_rate)))

    # Notation A-F
    if   score >= 95: grade = "A"
    elif score >= 85: grade = "B"
    elif score >= 70: grade = "C"
    elif score >= 55: grade = "D"
    else:             grade = "F"

    return {
        "score":        score,
        "grade":        grade,
        "total_jobs":   total,
        "successful":   len(successful),
        "failed":       len(failed),
        "canceled":     len(canceled),
        "success_rate": success_rate,
        "failure_rate": failure_rate,
    }


# ── CSV Export ────────────────────────────────────────────────────────────────

@router.get(
    "/export/jobs.csv",
    summary="Export job history as CSV",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
    dependencies=[Depends(require_auth)],
)
async def export_jobs_csv(
    request: Request,
    days: int = Query(default=30, ge=1, le=365, description="Plage temporelle en jours"),
):
    """
    Télécharge l'historique des jobs AWX des N derniers jours au format CSV RFC-4180.

    **Colonnes :**
    id, name, status, job_type, started, finished, elapsed_seconds, elapsed_human,
    failed, job_template, launched_by, execution_node

    **Paramètres :**
    - `days` : plage temporelle (défaut 30, max 365)
    """
    raw_jobs = await _get_jobs(request.app.state.http, days=days)
    jobs = [_enrich_job(j) for j in raw_jobs]

    fields = [
        "id", "name", "status", "job_type",
        "started", "finished", "elapsed_seconds", "elapsed_human",
        "failed", "job_template", "launched_by", "execution_node",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(jobs)
    csv_content = buf.getvalue()

    filename = f"autoflow_jobs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Score ─────────────────────────────────────────────────────────────────────

@router.get(
    "/score",
    summary="Current compliance score",
    dependencies=[Depends(require_auth)],
)
async def compliance_score(
    request: Request,
    days: int = Query(default=30, ge=1, le=365, description="Plage temporelle en jours"),
):
    """
    Retourne le score de conformité Autoflow (0-100) basé sur les résultats
    des jobs AWX des N derniers jours.

    **Grille de notation :**
    - A ≥ 95 %   B ≥ 85 %   C ≥ 70 %   D ≥ 55 %   F < 55 %
    """
    raw_jobs = await _get_jobs(request.app.state.http, days=days)
    jobs = [_enrich_job(j) for j in raw_jobs]
    score = _compute_score(jobs)
    score["period_days"]  = days
    score["generated_at"] = datetime.now(timezone.utc).isoformat()
    return score


# ── HTML Report ───────────────────────────────────────────────────────────────

_REPORT_CSS = """
body{font-family:'Segoe UI',Arial,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:0}
.header{background:linear-gradient(135deg,#1f6feb 0%,#388bfd 100%);padding:32px 40px;display:flex;align-items:center;gap:20px}
.header h1{margin:0;font-size:1.8rem;font-weight:700}
.header .subtitle{margin:4px 0 0;opacity:.8;font-size:.95rem}
.badge{background:rgba(255,255,255,.15);border-radius:8px;padding:4px 14px;font-size:.85rem;margin-left:auto}
.content{padding:32px 40px;max-width:1100px;margin:0 auto}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:32px}
.kpi{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px;text-align:center}
.kpi .value{font-size:2.4rem;font-weight:700;line-height:1}
.kpi .label{color:#8b949e;font-size:.8rem;margin-top:6px;text-transform:uppercase;letter-spacing:.05em}
.kpi.score-a .value{color:#3fb950}
.kpi.score-b .value{color:#a5d65e}
.kpi.score-c .value{color:#d29922}
.kpi.score-d .value{color:#e07b39}
.kpi.score-f .value{color:#f85149}
.kpi.ok .value{color:#3fb950}
.kpi.warn .value{color:#d29922}
.kpi.bad .value{color:#f85149}
.section{margin-bottom:32px}
.section h2{font-size:1.1rem;font-weight:600;color:#58a6ff;border-bottom:1px solid #21262d;padding-bottom:8px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{background:#21262d;color:#8b949e;padding:8px 12px;text-align:left;font-weight:500;text-transform:uppercase;letter-spacing:.04em}
td{padding:8px 12px;border-bottom:1px solid #21262d}
tr:last-child td{border-bottom:none}
tr:hover td{background:#161b22}
.badge-status{padding:2px 10px;border-radius:20px;font-size:.75rem;font-weight:500}
.s-successful{background:#1a3628;color:#3fb950}
.s-failed,.s-error{background:#3d1a1a;color:#f85149}
.s-canceled{background:#2d2d1a;color:#d29922}
.s-running{background:#1a2d3d;color:#79c0ff}
.s-pending,.s-waiting{background:#1e1e2e;color:#8b949e}
.footer{text-align:center;color:#8b949e;font-size:.75rem;padding:24px;border-top:1px solid #21262d;margin-top:32px}
.score-circle{width:80px;height:80px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.8rem;font-weight:700;margin:0 auto 8px;border:3px solid currentColor}
"""


def _build_html_report(jobs: list[dict], days: int) -> str:
    score_data = _compute_score(jobs)
    score      = score_data["score"]
    grade      = score_data["grade"]
    grade_cls  = f"score-{grade.lower()}"

    now_str     = datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC")
    period_from = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d/%m/%Y")
    period_to   = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    # Top N jobs en échec
    failed_jobs = [j for j in jobs if j.get("status") in ("failed", "error")][:20]
    # Tous les jobs (derniers 50 pour affichage)
    recent_jobs = jobs[:50]

    def status_badge(st: str) -> str:
        cls = f"s-{st}" if st in ("successful","failed","error","canceled","running","pending","waiting") else ""
        return f'<span class="badge-status {cls}">{st}</span>'

    # Tableau des jobs récents
    rows_recent = ""
    for j in recent_jobs:
        rows_recent += (
            f'<tr>'
            f'<td>{j["id"]}</td>'
            f'<td>{j["job_template"] or j["name"]}</td>'
            f'<td>{status_badge(j["status"])}</td>'
            f'<td>{j["job_type"]}</td>'
            f'<td>{(j["started"] or "")[:16].replace("T"," ")}</td>'
            f'<td>{j["elapsed_human"]}</td>'
            f'<td>{j["launched_by"] or "—"}</td>'
            f'</tr>'
        )

    # Tableau jobs en échec
    rows_failed = ""
    for j in failed_jobs:
        rows_failed += (
            f'<tr>'
            f'<td>{j["id"]}</td>'
            f'<td>{j["job_template"] or j["name"]}</td>'
            f'<td>{status_badge(j["status"])}</td>'
            f'<td>{(j["started"] or "")[:16].replace("T"," ")}</td>'
            f'<td>{j["elapsed_human"]}</td>'
            f'<td>{j["launched_by"] or "—"}</td>'
            f'</tr>'
        )

    failed_section = ""
    if failed_jobs:
        failed_section = f"""
        <div class="section">
          <h2>⚠️ Jobs en échec ({len(failed_jobs)})</h2>
          <table>
            <tr><th>#</th><th>Template</th><th>Statut</th><th>Démarré</th><th>Durée</th><th>Lancé par</th></tr>
            {rows_failed}
          </table>
        </div>"""

    # KPIs additionnels
    success_kpi_cls  = "ok" if score_data["success_rate"] >= 95 else ("warn" if score_data["success_rate"] >= 70 else "bad")
    failure_kpi_cls  = "ok" if score_data["failure_rate"] < 5  else ("warn" if score_data["failure_rate"] < 15 else "bad")

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rapport de Conformité Autoflow — {now_str}</title>
  <style>{_REPORT_CSS}</style>
</head>
<body>
  <div class="header">
    <div>
      <h1>🛡️ Rapport de Conformité Autoflow</h1>
      <p class="subtitle">Période : {period_from} → {period_to} ({days} jours)</p>
    </div>
    <div class="badge">Généré le {now_str}</div>
  </div>

  <div class="content">

    <!-- KPIs -->
    <div class="kpi-grid">
      <div class="kpi {grade_cls}">
        <div class="score-circle" style="color:inherit">{grade}</div>
        <div class="value" style="font-size:1.6rem">{score}/100</div>
        <div class="label">Score Global</div>
      </div>
      <div class="kpi {success_kpi_cls}">
        <div class="value">{score_data['success_rate']}%</div>
        <div class="label">Taux de Réussite</div>
      </div>
      <div class="kpi {failure_kpi_cls}">
        <div class="value">{score_data['failure_rate']}%</div>
        <div class="label">Taux d'Échec</div>
      </div>
      <div class="kpi">
        <div class="value" style="color:#58a6ff">{score_data['total_jobs']}</div>
        <div class="label">Jobs Exécutés</div>
      </div>
      <div class="kpi ok">
        <div class="value">{score_data['successful']}</div>
        <div class="label">Réussis</div>
      </div>
      <div class="kpi {'bad' if score_data['failed'] > 0 else 'ok'}">
        <div class="value">{score_data['failed']}</div>
        <div class="label">Échoués</div>
      </div>
    </div>

    <!-- Jobs récents -->
    <div class="section">
      <h2>📋 Historique récent (50 derniers jobs)</h2>
      <table>
        <tr><th>#</th><th>Template</th><th>Statut</th><th>Type</th><th>Démarré</th><th>Durée</th><th>Lancé par</th></tr>
        {rows_recent if rows_recent else '<tr><td colspan="7" style="text-align:center;color:#8b949e">Aucun job sur la période</td></tr>'}
      </table>
    </div>

    {failed_section}

  </div>

  <div class="footer">
    Autoflow Compliance Report — Généré automatiquement par le service API Autoflow
    &nbsp;|&nbsp; {now_str}
  </div>
</body>
</html>"""


async def _generate_and_cache(http) -> str:
    """Génère le rapport HTML, le met en cache sur disque et retourne le contenu."""
    raw_jobs = await _get_jobs(http, days=30)
    jobs     = [_enrich_job(j) for j in raw_jobs]
    html     = _build_html_report(jobs, days=30)
    try:
        _REPORT_CACHE_FILE.write_text(html, encoding="utf-8")
        logger.info("Rapport de conformité mis en cache : %s", _REPORT_CACHE_FILE)
    except OSError as exc:
        logger.warning("Impossible d'écrire le cache du rapport : %s", exc)
    return html


# ── Endpoints HTML ────────────────────────────────────────────────────────────

@router.get(
    "/report",
    summary="On-demand HTML compliance report",
    response_class=HTMLResponse,
    dependencies=[Depends(require_auth)],
)
async def compliance_report(
    request: Request,
    days: int = Query(default=30, ge=1, le=365, description="Plage temporelle en jours"),
):
    """
    Génère et retourne un rapport HTML de conformité à la demande.

    Le rapport contient :
    - Score global (0-100) et grade (A-F)
    - KPIs : taux de réussite/échec, nombre de jobs
    - Tableau des 50 derniers jobs
    - Tableau des jobs en échec

    **Paramètres :**
    - `days` : plage temporelle (défaut 30, max 365)
    """
    raw_jobs = await _get_jobs(request.app.state.http, days=days)
    jobs     = [_enrich_job(j) for j in raw_jobs]
    html     = _build_html_report(jobs, days=days)
    # Mise en cache uniquement pour la période par défaut (30j)
    if days == 30:
        try:
            _REPORT_CACHE_FILE.write_text(html, encoding="utf-8")
        except OSError:
            pass
    return HTMLResponse(content=html)


@router.get(
    "/report/latest",
    summary="Last auto-generated compliance report",
    response_class=HTMLResponse,
    dependencies=[Depends(require_auth)],
)
async def compliance_report_latest(request: Request):
    """
    Retourne le dernier rapport HTML généré automatiquement (cache disque).

    Si aucun rapport n'a encore été généré, en produit un à la volée.
    """
    if _REPORT_CACHE_FILE.exists():
        html = _REPORT_CACHE_FILE.read_text(encoding="utf-8")
        return HTMLResponse(content=html)
    # Pas encore de cache → génération à la volée
    html = await _generate_and_cache(request.app.state.http)
    return HTMLResponse(content=html)


@router.post(
    "/report/generate",
    summary="Trigger immediate compliance report generation",
    dependencies=[Depends(require_auth)],
)
async def generate_compliance_report(request: Request):
    """
    Déclenche immédiatement la génération du rapport de conformité et le met en cache.

    Utile pour forcer une mise à jour avant la prochaine exécution hebdomadaire.
    Retourne l'URL pour accéder au rapport généré.
    """
    await _generate_and_cache(request.app.state.http)
    return {
        "status":       "generated",
        "report_url":   "/compliance/report/latest",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
