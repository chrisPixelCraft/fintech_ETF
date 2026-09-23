"""Cached, causal B/C transforms for the predeclared tuning grid.

This module is intentionally separate from the frozen v2 implementation.  It
reproduces the frozen B/C rules at the baseline parameter values, while caching
the parameter-independent point-in-time state used by repeated trials.

The grid is development-only.  Choosing a winner after evaluating several
configurations creates selection bias; the selected configuration therefore
needs a later, untouched evaluation period.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HISTORY = ROOT / "data/sector/industry_history.csv"
DEFAULT_INDICES = ROOT / "data/sector/sector_index_daily.csv"
DEFAULT_CALENDAR = ROOT / "data/v2/market_daily.csv"
DEFAULT_AUXILIARY = ROOT / "data/v2_auxiliary"
DEFAULT_AUX_CONFIG = ROOT / "config/v2_auxiliary_signals.json"

SECTOR_TOP_FRACTIONS = (0.25, 0.5, 0.75, 1.0)
SECTOR_SHORT_WEIGHTS = (0.0, 0.5, 8.0 / 15.0, 1.0)
SECTOR_FALLBACK_MODES = ("baseline", "total_eligible")
C_ALPHAS = (0.0, 0.02, 0.08, 0.16, 0.30)


def _day(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("Asia/Taipei").tz_localize(None)
    return stamp.normalize()


def _choice(value: float, choices: tuple[float, ...], name: str) -> float:
    for candidate in choices:
        if np.isclose(float(value), candidate, atol=1e-12, rtol=0.0):
            return candidate
    raise ValueError(f"{name}={value!r} is outside the predeclared grid {choices}")


@dataclass(frozen=True)
class BCParameters:
    """One predeclared B/C trial configuration."""

    sector_top_fraction: float = 0.5
    sector_short_weight: float = 8.0 / 15.0
    sector_fallback_mode: str = "baseline"
    c_alpha: float = 0.08

    def validated(self) -> "BCParameters":
        fallback = str(self.sector_fallback_mode)
        if fallback not in SECTOR_FALLBACK_MODES:
            raise ValueError(
                f"sector_fallback_mode={fallback!r} is outside {SECTOR_FALLBACK_MODES}"
            )
        return BCParameters(
            sector_top_fraction=_choice(
                self.sector_top_fraction, SECTOR_TOP_FRACTIONS, "sector_top_fraction"
            ),
            sector_short_weight=_choice(
                self.sector_short_weight, SECTOR_SHORT_WEIGHTS, "sector_short_weight"
            ),
            sector_fallback_mode=fallback,
            c_alpha=_choice(self.c_alpha, C_ALPHAS, "c_alpha"),
        )

    def as_dict(self) -> dict[str, float | str]:
        return {
            "sector_top_fraction": self.sector_top_fraction,
            "sector_short_weight": self.sector_short_weight,
            "sector_fallback_mode": self.sector_fallback_mode,
            "c_alpha": self.c_alpha,
        }


@dataclass(frozen=True)
class _SectorState:
    cutoff: pd.Timestamp
    mapping: dict[str, str]
    stats: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _OvernightState:
    trade_day: pd.Timestamp | None
    cutoff: pd.Timestamp | None
    status: str
    values: dict[str, float]
    observations: dict[str, Any]


class TuningSignals:
    """Share causal sector/overnight caches across repeated B/C trials.

    Construct one instance per input snapshot, then create cheap callbacks with
    :meth:`callback`.  The callback has the engine signature
    ``(signal_day, ranked) -> (frame, metadata)``.
    """

    def __init__(
        self,
        calendar_path: str | Path = DEFAULT_CALENDAR,
        history_path: str | Path = DEFAULT_HISTORY,
        index_path: str | Path = DEFAULT_INDICES,
        auxiliary_dir: str | Path = DEFAULT_AUXILIARY,
        auxiliary_config_path: str | Path = DEFAULT_AUX_CONFIG,
    ) -> None:
        self.calendar_path = Path(calendar_path)
        self.history_path = Path(history_path)
        self.index_path = Path(index_path)
        self.auxiliary_dir = Path(auxiliary_dir)
        self.auxiliary_config_path = Path(auxiliary_config_path)

        self.sessions = self._load_sessions(self.calendar_path)
        self.history = pd.read_csv(self.history_path, dtype={"symbol": str, "sector_id": str})
        self.history["known_at"] = pd.to_datetime(
            self.history["known_at"], utc=True, errors="coerce"
        )
        for column in ("effective_from", "effective_to"):
            self.history[column] = pd.to_datetime(self.history[column], errors="coerce")

        indices = pd.read_csv(self.index_path, dtype={"sector_id": str})
        if "index_type" in indices:
            indices = indices[indices["index_type"].eq("TOTAL_RETURN")].copy()
        indices = indices[
            ~indices["sector_id"].astype(str).str.contains(
                r"(?:_|:)(?:07|13)$", regex=True
            )
        ].copy()
        indices["date"] = pd.to_datetime(indices["date"]).dt.normalize()
        indices["available_at"] = pd.to_datetime(
            indices["available_at"], utc=True, errors="coerce"
        )
        self.indices = indices.sort_values(["sector_id", "date"])
        self._index_groups = {
            str(sector): rows.reset_index(drop=True)
            for sector, rows in self.indices.groupby("sector_id", sort=True)
        }

        config_raw = self.auxiliary_config_path.read_bytes()
        self.aux_config = json.loads(config_raw)
        self.aux_config_sha256 = hashlib.sha256(config_raw).hexdigest()
        overnight_path = self.auxiliary_dir / "overnight_daily.csv"
        self.overnight = (
            pd.read_csv(overnight_path, dtype={"instrument": str})
            if overnight_path.exists()
            else pd.DataFrame()
        )
        if len(self.overnight):
            required = {
                "instrument", "session_date", "return1", "available_at",
                "source_url", "source_sha256",
            }
            if required - set(self.overnight):
                raise ValueError(
                    f"overnight_daily.csv missing {sorted(required - set(self.overnight))}"
                )
            self.overnight["available_at"] = pd.to_datetime(
                self.overnight["available_at"], utc=True
            )
            self.overnight["session_date"] = pd.to_datetime(
                self.overnight["session_date"]
            ).dt.normalize()
            if self.overnight.duplicated(["instrument", "session_date"]).any():
                raise ValueError("Duplicate overnight instrument/session rows")
            if not np.isfinite(self.overnight["return1"].to_numpy(float)).all():
                raise ValueError("Nonfinite overnight returns")
        self._overnight_groups = {
            str(instrument): rows.sort_values(["available_at", "session_date"]).reset_index(drop=True)
            for instrument, rows in self.overnight.groupby("instrument", sort=False)
        } if len(self.overnight) else {}
        status_path = self.auxiliary_dir / "source_status.json"
        self.source_status = json.loads(status_path.read_text()) if status_path.exists() else {}

        self._sector_cache: dict[pd.Timestamp, _SectorState] = {}
        self._overnight_cache: dict[pd.Timestamp, _OvernightState] = {}
        self._overnight_stock_cache: dict[pd.Timestamp, dict[str, tuple[str, float]]] = {}
        self._hits = {"sector": 0, "overnight": 0, "overnight_stock": 0}
        self._misses = {"sector": 0, "overnight": 0, "overnight_stock": 0}

    @staticmethod
    def _load_sessions(path: Path) -> pd.DatetimeIndex:
        frame = pd.read_csv(path, usecols=["date"])
        sessions = pd.DatetimeIndex(
            pd.to_datetime(frame["date"]).dt.normalize().unique()
        ).sort_values()
        if sessions.empty or sessions.has_duplicates:
            raise ValueError("Calendar must contain at least one unique session")
        return sessions

    def callback(
        self,
        version: str,
        *,
        target_count: int = 25,
        min_count: int = 20,
        params: BCParameters | None = None,
        sector_top_fraction: float = 0.5,
        sector_short_weight: float = 8.0 / 15.0,
        sector_fallback_mode: str = "baseline",
        c_alpha: float = 0.08,
    ) -> Callable[[Any, pd.DataFrame], tuple[pd.DataFrame, dict[str, Any]]]:
        """Return an engine callback bound to one trial's parameters."""
        trial = params or BCParameters(
            sector_top_fraction=sector_top_fraction,
            sector_short_weight=sector_short_weight,
            sector_fallback_mode=sector_fallback_mode,
            c_alpha=c_alpha,
        )
        trial = trial.validated()
        version = version.upper()
        if version not in {"B", "C"}:
            raise ValueError("TuningSignals supports only B and C")
        if target_count <= 0 or min_count <= 0 or min_count > target_count:
            raise ValueError("Require target_count >= min_count > 0")

        def apply(signal_day: Any, ranked: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
            return self.transform(
                version,
                signal_day,
                ranked,
                target_count=target_count,
                min_count=min_count,
                params=trial,
            )

        return apply

    def make_callback(
        self,
        version: str,
        params: BCParameters | dict[str, Any],
    ) -> Callable[[Any, pd.DataFrame], tuple[pd.DataFrame, dict[str, Any]]]:
        """Bind a flat runner parameter dictionary to an engine callback.

        This alias keeps trial construction cheap: the loaded inputs and all
        prewarmed state stay on this object, while only an immutable parameter
        record is captured by the returned closure.
        """
        if isinstance(params, BCParameters):
            return self.callback(version, params=params)
        if not isinstance(params, dict):
            raise TypeError("params must be BCParameters or a flat dictionary")
        allowed = {
            "target_count", "min_count", "sector_top_fraction",
            "sector_short_weight", "sector_fallback_mode", "c_alpha",
        }
        unexpected = set(params) - allowed
        if unexpected:
            raise ValueError(f"Unexpected tuning parameter keys: {sorted(unexpected)}")
        target_count = int(params.get("target_count", 25))
        min_count = int(params.get("min_count", 20))
        trial = BCParameters(
            sector_top_fraction=float(params.get("sector_top_fraction", 0.5)),
            sector_short_weight=float(params.get("sector_short_weight", 8.0 / 15.0)),
            sector_fallback_mode=str(params.get("sector_fallback_mode", "baseline")),
            c_alpha=float(params.get("c_alpha", 0.08)),
        )
        return self.callback(
            version,
            target_count=target_count,
            min_count=min_count,
            params=trial,
        )

    def prewarm(
        self,
        signal_days: Any,
        symbols: Any | None = None,
    ) -> dict[str, Any]:
        """Populate causal day caches before a fork-based process pool starts.

        Supplying the fixed historical universe also materializes C's per-stock
        vectors.  No row later than each day's next-session cutoff is read.
        """
        unique_days = sorted({_day(value) for value in signal_days})
        symbol_list = None if symbols is None else list(dict.fromkeys(str(x) for x in symbols))
        for day in unique_days:
            sector_state = self._sector_state(day)
            overnight_state = self._overnight_state(day)
            if symbol_list is None or overnight_state.status == "NO_NEXT_SESSION":
                continue
            cached = self._overnight_stock_cache.setdefault(day, {})
            for symbol in symbol_list:
                sector_id = str(sector_state.mapping.get(symbol, "UNKNOWN"))
                if symbol not in cached:
                    cached[symbol] = (
                        sector_id,
                        self._overnight_score(sector_id, overnight_state.values),
                    )
        return self.cache_info()

    def transform(
        self,
        version: str,
        signal_day: Any,
        ranked: pd.DataFrame,
        *,
        target_count: int = 25,
        min_count: int = 20,
        params: BCParameters | None = None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Apply a parameterized B or C rule using cached causal inputs."""
        version = version.upper()
        if version not in {"B", "C"}:
            raise ValueError("TuningSignals supports only B and C")
        trial = (params or BCParameters()).validated()
        if target_count <= 0 or min_count <= 0 or min_count > target_count:
            raise ValueError("Require target_count >= min_count > 0")
        required = {"symbol", "score", "entry_ok"}
        if required - set(ranked):
            raise ValueError(f"ranked frame missing {sorted(required - set(ranked))}")

        day = _day(signal_day)
        out, meta = self._apply_sector(
            day, ranked, target_count=target_count, min_count=min_count, params=trial
        )
        if version == "C":
            out, auxiliary_meta = self._apply_overnight(day, out, trial.c_alpha)
            meta.update(auxiliary_meta)
        meta["tuning_parameters"] = {
            **trial.as_dict(),
            "target_count": int(target_count),
            "min_count": int(min_count),
        }
        meta["tuning_cache_keys"] = {
            "sector": str(day.date()),
            "overnight": str(day.date()) if version == "C" else None,
        }
        return out, meta

    def cache_info(self) -> dict[str, Any]:
        """Return cache sizes and hit/miss counters for runner provenance."""
        return {
            "entries": {
                "sector": len(self._sector_cache),
                "overnight": len(self._overnight_cache),
                "overnight_stock_days": len(self._overnight_stock_cache),
                "overnight_stock_rows": sum(len(x) for x in self._overnight_stock_cache.values()),
            },
            "hits": dict(self._hits),
            "misses": dict(self._misses),
        }

    def _sector_state(self, day: pd.Timestamp) -> _SectorState:
        cached = self._sector_cache.get(day)
        if cached is not None:
            self._hits["sector"] += 1
            return cached
        self._misses["sector"] += 1
        later = self.sessions[self.sessions > day]
        next_day = pd.Timestamp(later[0]) if len(later) else day + pd.offsets.BDay()
        cutoff = (
            next_day.tz_localize("Asia/Taipei")
            + pd.Timedelta(hours=8, minutes=55)
        ).tz_convert("UTC")
        valid = self.history[
            (self.history["effective_from"] <= day)
            & (self.history["effective_to"].isna() | (day < self.history["effective_to"]))
            & (self.history["known_at"] <= cutoff)
        ]
        if valid["symbol"].duplicated().any():
            raise ValueError("Overlapping point-in-time sector classifications")
        mapping = valid.set_index("symbol")["sector_id"].to_dict()

        expected = self.sessions[self.sessions <= day][-51:]
        stats: list[dict[str, Any]] = []
        for sector, source_rows in self._index_groups.items():
            rows = source_rows[
                (source_rows["date"] <= day)
                & (source_rows["available_at"] <= cutoff)
            ].drop_duplicates("date", keep="last")
            prices = rows["close"].astype(float)
            if len(prices) < 51 or not np.isfinite(prices).all() or (prices <= 0).any():
                continue
            if len(expected) < 51 or not pd.DatetimeIndex(rows["date"].tail(51)).equals(expected):
                continue
            stats.append({
                "sector_id": sector,
                "return20": prices.iloc[-1] / prices.iloc[-21] - 1.0,
                "return50": prices.iloc[-1] / prices.iloc[-51] - 1.0,
                "trend": bool(
                    prices.iloc[-1]
                    > prices.ewm(span=20, adjust=False).mean().iloc[-1]
                    > prices.ewm(span=50, adjust=False).mean().iloc[-1]
                ),
                "last_date": str(rows["date"].iloc[-1].date()),
            })
        state = _SectorState(cutoff=cutoff, mapping=mapping, stats=tuple(stats))
        self._sector_cache[day] = state
        return state

    def _apply_sector(
        self,
        day: pd.Timestamp,
        ranked: pd.DataFrame,
        *,
        target_count: int,
        min_count: int,
        params: BCParameters,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        state = self._sector_state(day)
        out = ranked.copy()
        out["base_score"] = out["score"]
        out["sector_id"] = out["symbol"].map(state.mapping).fillna("UNKNOWN")
        out["sector_score"] = 0.5
        out["sector_gate"] = True
        meta: dict[str, Any] = {
            "sector_cutoff": state.cutoff.isoformat(),
            "sector_unknown_count": int(out["sector_id"].eq("UNKNOWN").sum()),
            "sector_fallback": [],
        }
        if not state.stats:
            out["sector_status"] = "UNKNOWN_NEUTRAL_BASELINE_FALLBACK"
            meta.update(
                sector_status="UNKNOWN_NEUTRAL_BASELINE_FALLBACK",
                sector_fallback=["NO_PIT_SECTOR_DATA"],
            )
            return out, meta

        sectors = pd.DataFrame(state.stats).set_index("sector_id")
        short = params.sector_short_weight
        sectors["score"] = (
            short * sectors["return20"].rank(pct=True)
            + (1.0 - short) * sectors["return50"].rank(pct=True)
        )
        sectors = sectors.sort_values(["score", "sector_id"], ascending=[False, True])
        top_n = int(np.ceil(len(sectors) * params.sector_top_fraction))
        upper = set(sectors.index[:top_n])
        allowed = {sector for sector in upper if bool(sectors.at[sector, "trend"])}
        known = out["sector_id"].isin(sectors.index)
        baseline_eligible = out["entry_ok"].astype(bool)

        def eligible_count() -> int:
            selected = out["sector_id"].isin(allowed)
            if params.sector_fallback_mode == "total_eligible":
                selected = selected | ~known
            return int((baseline_eligible & selected).sum())

        if eligible_count() < target_count:
            for sector in sectors.index:
                if sector not in allowed and bool(sectors.at[sector, "trend"]):
                    allowed.add(sector)
                    meta["sector_fallback"].append("SECTOR_RANK_FALLBACK:" + sector)
                    if eligible_count() >= target_count:
                        break
        if eligible_count() < min_count:
            for sector in sectors.index:
                if sector not in allowed:
                    allowed.add(sector)
                    meta["sector_fallback"].append("WEAK_MARKET_FALLBACK:" + sector)
                    if eligible_count() >= min_count:
                        break

        # Unknown taxonomy/index rows stay neutral and pass the gate.  They are
        # counted only by the explicitly selected total_eligible fallback mode.
        out["sector_gate"] = ~known | out["sector_id"].isin(allowed)
        out["entry_ok"] = out["entry_ok"].astype(bool) & out["sector_gate"]
        out["sector_score"] = out["sector_id"].map(sectors["score"]).fillna(0.5)
        stale = {
            sector for sector in sectors.index
            if (day - pd.Timestamp(sectors.at[sector, "last_date"])).days > 7
        }
        out["sector_status"] = np.where(
            known,
            np.where(out["sector_id"].isin(stale), "STALE_INDEX", "AVAILABLE"),
            "UNKNOWN_NEUTRAL_BASELINE_FALLBACK",
        )
        meta.update(
            sector_status="PARTIAL" if (~known).any() or stale else "AVAILABLE",
            sector_known_count=int(known.sum()),
            sector_allowed=sorted(allowed),
            stale_sectors=sorted(stale),
            sector_scores=sectors.reset_index().to_dict("records"),
        )
        return out, meta

    def _next_session_cutoff(
        self, day: pd.Timestamp
    ) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        later = self.sessions[self.sessions > day]
        if not len(later):
            return None, None
        trade_day = pd.Timestamp(later[0])
        hours, minutes = (
            int(value) for value in self.aux_config["decision_cutoff"].split(":")
        )
        cutoff = trade_day.tz_localize(
            self.aux_config["decision_timezone"]
        ) + pd.Timedelta(hours=hours, minutes=minutes)
        return trade_day, cutoff

    def _overnight_state(self, day: pd.Timestamp) -> _OvernightState:
        cached = self._overnight_cache.get(day)
        if cached is not None:
            self._hits["overnight"] += 1
            return cached
        self._misses["overnight"] += 1
        trade_day, cutoff = self._next_session_cutoff(day)
        if trade_day is None or cutoff is None:
            state = _OvernightState(None, None, "NO_NEXT_SESSION", {}, {})
            self._overnight_cache[day] = state
            return state
        cutoff_utc = cutoff.tz_convert("UTC")
        values: dict[str, float] = {}
        observations: dict[str, Any] = {}
        observed = 0
        instruments = self.aux_config["overnight_instruments"]
        for instrument, spec in instruments.items():
            rows = self._overnight_groups.get(instrument)
            eligible = rows[rows["available_at"] <= cutoff_utc] if rows is not None else pd.DataFrame()
            if not len(eligible):
                values[instrument] = 0.0
                observations[instrument] = {
                    "status": "UNKNOWN",
                    "reason": self._source_reason(instrument),
                }
                continue
            row = eligible.iloc[-1]
            age_hours = (cutoff_utc - row["available_at"]).total_seconds() / 3600.0
            if age_hours > float(spec["max_age_hours"]):
                values[instrument] = 0.0
                observations[instrument] = {
                    "status": "STALE",
                    "available_at": row["available_at"].isoformat(),
                    "age_hours": age_hours,
                    "source_sha256": row["source_sha256"],
                }
                continue
            value = float(
                np.clip(float(row["return1"]) / float(spec["scale_return"]), -1.0, 1.0)
                * float(spec["direction"])
            )
            values[instrument] = value
            observed += 1
            observations[instrument] = {
                "status": "OBSERVED",
                "session_date": str(row["session_date"].date()),
                "available_at": row["available_at"].isoformat(),
                "return1": float(row["return1"]),
                "normalized": value,
                "source_url": row["source_url"],
                "source_sha256": row["source_sha256"],
            }
        status = "OBSERVED" if observed == len(instruments) else (
            "UNKNOWN" if observed == 0 else "PARTIAL"
        )
        state = _OvernightState(trade_day, cutoff, status, values, observations)
        self._overnight_cache[day] = state
        return state

    def _source_reason(self, instrument: str) -> str:
        sources = self.source_status.get("sources", {}) if isinstance(self.source_status, dict) else {}
        record = sources.get(instrument, {})
        return record.get("reason") or record.get("status") or "NO_ELIGIBLE_POINT_IN_TIME_ROW"

    def _sector_categories(self, sector_id: Any) -> set[str]:
        raw = str(sector_id).strip()
        token = raw.replace(":", "_").split("_")[-1]
        categories = {"all"}
        tech = {str(value) for value in self.aux_config["technology_sector_ids"]}
        semi = {str(value) for value in self.aux_config["semiconductor_sector_ids"]}
        upper = raw.upper()
        if token in tech or any(value in upper for value in ("電子", "半導體", "TECH", "ELECTRON")):
            categories.add("technology")
        if token in semi or "半導體" in upper or "SEMICONDUCT" in upper:
            categories.update(("technology", "semiconductor"))
        return categories

    def _overnight_score(self, sector_id: Any, values: dict[str, float]) -> float:
        categories = self._sector_categories(sector_id)
        numerator = denominator = 0.0
        for instrument, spec in self.aux_config["overnight_instruments"].items():
            applicable = sum(
                float(weight)
                for category, weight in spec.get("weights", {}).items()
                if category == "all" or category in categories
            )
            numerator += applicable * values[instrument]
            denominator += abs(applicable)
        return numerator / denominator if denominator else 0.0

    def _stock_overnight_scores(
        self, day: pd.Timestamp, frame: pd.DataFrame, state: _OvernightState
    ) -> pd.Series:
        cached = self._overnight_stock_cache.setdefault(day, {})
        scores: list[float] = []
        for symbol, sector_id in zip(frame["symbol"].astype(str), frame["sector_id"]):
            prior = cached.get(symbol)
            sector_text = str(sector_id)
            if prior is not None:
                if prior[0] != sector_text:
                    raise ValueError(f"Causal sector mapping changed within {day.date()} for {symbol}")
                self._hits["overnight_stock"] += 1
                scores.append(prior[1])
                continue
            self._misses["overnight_stock"] += 1
            value = self._overnight_score(sector_id, state.values)
            cached[symbol] = (sector_text, value)
            scores.append(value)
        return pd.Series(scores, index=frame.index, dtype=float)

    def _apply_overnight(
        self, day: pd.Timestamp, ranked: pd.DataFrame, alpha: float
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        out = ranked.copy()
        protected = {
            column: out[column].copy() for column in ("entry_ok", "exit") if column in out
        }
        state = self._overnight_state(day)
        if state.status == "NO_NEXT_SESSION":
            out["aux_data_status"] = state.status
            return out, {
                "version": "C",
                "signal_date": str(day.date()),
                "trade_date": None,
                "cutoff_at": None,
                "config_sha256": self.aux_config_sha256,
                "mutation_scope": "none",
                "data_status": {"calendar": state.status},
                "observations": {},
                "overall_data_status": state.status,
                "skip_order": True,
                "block_reason": state.status,
            }
        overnight = self._stock_overnight_scores(day, out, state)
        out["overnight_score"] = overnight
        base = out["base_score"].astype(float)
        out["c_score"] = base if alpha == 0.0 else base + alpha * overnight
        out["score"] = out["c_score"]
        for column, original in protected.items():
            if not out[column].equals(original):
                raise AssertionError(f"Overnight transform changed protected column {column}")
        degraded = [] if state.status == "OBSERVED" else [f"overnight:{state.status}"]
        overall = "READY" if not degraded else "DEGRADED_" + "|".join(degraded)
        out["aux_data_status"] = overall
        return out, {
            "version": "C",
            "signal_date": str(day.date()),
            "trade_date": str(state.trade_day.date()),
            "cutoff_at": state.cutoff.isoformat(),
            "config_sha256": self.aux_config_sha256,
            "mutation_scope": "score_only",
            "data_status": {"overnight": state.status},
            "observations": {"overnight": state.observations},
            "overall_data_status": overall,
        }


__all__ = [
    "BCParameters",
    "C_ALPHAS",
    "SECTOR_FALLBACK_MODES",
    "SECTOR_SHORT_WEIGHTS",
    "SECTOR_TOP_FRACTIONS",
    "TuningSignals",
]
