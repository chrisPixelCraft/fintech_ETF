"""Causal auxiliary ranking transforms for v2 strategies C and D.

The sector gate belongs to the caller.  This module consumes B's ranked frame,
changes only the ranking score, and records every downgrade in returned meta.
It never mutates entry/exit flags or generates orders.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/v2_auxiliary_signals.json"
DEFAULT_AUXILIARY = ROOT / "data/v2_auxiliary"


def _date(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("Asia/Taipei").tz_localize(None)
    return stamp.normalize()


def _clip01(values: pd.Series | np.ndarray | float) -> Any:
    return np.clip(values, 0.0, 1.0)


class AuxiliarySignals:
    """Apply frozen C/D ranking transforms using point-in-time auxiliary data.

    Parameters
    ----------
    calendar_path:
        CSV containing a ``date`` column. Duplicate dates are allowed because a
        canonical stock panel may be supplied; only its unique sessions matter.
    auxiliary_dir:
        Directory containing ``overnight_daily.csv`` and optional PIT tables.
    config_path:
        Frozen JSON rule table. Its SHA-256 is returned in every meta record.
    """

    def __init__(
        self,
        calendar_path: str | Path,
        auxiliary_dir: str | Path = DEFAULT_AUXILIARY,
        config_path: str | Path = DEFAULT_CONFIG,
    ) -> None:
        self.calendar_path = Path(calendar_path)
        self.auxiliary_dir = Path(auxiliary_dir)
        self.config_path = Path(config_path)
        raw_config = self.config_path.read_bytes()
        self.config = json.loads(raw_config)
        self.config_sha256 = hashlib.sha256(raw_config).hexdigest()
        self.sessions = self._load_sessions(self.calendar_path)
        self.overnight = self._read_table("overnight_daily.csv", required=False)
        self.earnings = self._read_table("earnings_pit.csv", required=False)
        self.institutional = self._read_table("institutional_pit.csv", required=False)
        status_path = self.auxiliary_dir / "source_status.json"
        self.source_status = json.loads(status_path.read_text()) if status_path.exists() else {}
        self._validate_inputs()

    @staticmethod
    def _load_sessions(path: Path) -> pd.DatetimeIndex:
        frame = pd.read_csv(path, usecols=["date"])
        sessions = pd.DatetimeIndex(pd.to_datetime(frame["date"]).dt.normalize().unique()).sort_values()
        if sessions.empty or sessions.has_duplicates:
            raise ValueError("Calendar must contain at least one unique session")
        return sessions

    def _read_table(self, name: str, required: bool) -> pd.DataFrame:
        path = self.auxiliary_dir / name
        if not path.exists():
            if required:
                raise FileNotFoundError(path)
            return pd.DataFrame()
        try:
            return pd.read_csv(path, dtype={"symbol": str, "instrument": str})
        except pd.errors.EmptyDataError:
            return pd.DataFrame()

    def _validate_inputs(self) -> None:
        if len(self.overnight):
            needed = {"instrument", "session_date", "return1", "available_at", "source_url", "source_sha256"}
            if needed - set(self.overnight):
                raise ValueError(f"overnight_daily.csv missing {sorted(needed - set(self.overnight))}")
            self.overnight["available_at"] = pd.to_datetime(self.overnight["available_at"], utc=True)
            self.overnight["session_date"] = pd.to_datetime(self.overnight["session_date"]).dt.normalize()
            if self.overnight.duplicated(["instrument", "session_date"]).any():
                raise ValueError("Duplicate overnight instrument/session rows")
            if not np.isfinite(self.overnight["return1"].to_numpy(float)).all():
                raise ValueError("Nonfinite overnight returns")
        for name, frame in (("earnings", self.earnings), ("institutional", self.institutional)):
            if not len(frame):
                continue
            needed = {"symbol", "score", "available_at", "source_url", "source_sha256"}
            if needed - set(frame):
                raise ValueError(f"{name}_pit.csv missing {sorted(needed - set(frame))}")
            frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True)
            frame["symbol"] = frame["symbol"].astype(str)
            if (frame["score"].astype(float).abs() > 1.0 + 1e-12).any():
                raise ValueError(f"{name} scores must be in [-1, 1]")

    def next_session_cutoff(
        self, signal_date: Any
    ) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        signal_day = _date(signal_date)
        future = self.sessions[self.sessions > signal_day]
        if not len(future):
            return None, None
        trade_day = pd.Timestamp(future[0])
        hh, mm = (int(x) for x in self.config["decision_cutoff"].split(":"))
        cutoff = trade_day.tz_localize(self.config["decision_timezone"]) + pd.Timedelta(hours=hh, minutes=mm)
        return trade_day, cutoff

    def transformer(self, version: str):
        """Return an engine-compatible ``(signal_date, ranked)`` callback."""
        return lambda signal_date, ranked: self.modify(version, signal_date, ranked)

    def modify(
        self, version: str, signal_date: Any, ranked: pd.DataFrame
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Return a copied ranking frame and causal audit metadata.

        A/B are pass-throughs. C adds only the frozen overnight ranking term.
        D composes C then applies one frozen regime weight row. ``entry_ok`` and
        ``exit`` are asserted unchanged for all versions.
        """
        version = version.upper()
        if version not in {"A", "B", "C", "D"}:
            raise ValueError(f"Unknown v2 version: {version}")
        out = ranked.copy()
        score_col = self.config["score_column"]
        base_col = self.config["base_score_column"]
        if score_col not in out or "symbol" not in out:
            raise ValueError(f"Ranked frame requires symbol and {score_col}")
        if base_col not in out:
            out[base_col] = out[score_col].astype(float)
        protected = {name: out[name].copy() for name in ("entry_ok", "exit") if name in out}
        trade_day, cutoff = self.next_session_cutoff(signal_date)
        if trade_day is None or cutoff is None:
            status = "NO_NEXT_SESSION"
            out["aux_data_status"] = status
            return out, {
                "version": version,
                "signal_date": str(_date(signal_date).date()),
                "trade_date": None,
                "cutoff_at": None,
                "config_sha256": self.config_sha256,
                "mutation_scope": "none",
                "data_status": {"calendar": status},
                "observations": {},
                "overall_data_status": status,
                "skip_order": True,
                "block_reason": status,
            }
        meta: dict[str, Any] = {
            "version": version,
            "signal_date": str(_date(signal_date).date()),
            "trade_date": str(trade_day.date()),
            "cutoff_at": cutoff.isoformat(),
            "config_sha256": self.config_sha256,
            "mutation_scope": "score_only",
            "data_status": {},
            "observations": {},
        }
        if version in {"A", "B"}:
            meta["overall_data_status"] = "NOT_USED"
            return out, meta

        overnight, overnight_meta = self._overnight_scores(out, cutoff)
        out["overnight_score"] = overnight
        out["c_score"] = out[base_col].astype(float) + self.config["c_score_adjustment"] * overnight
        out[score_col] = out["c_score"]
        meta["data_status"]["overnight"] = overnight_meta["status"]
        meta["observations"]["overnight"] = overnight_meta["observations"]
        if version == "D":
            out, d_meta = self._apply_d(out, cutoff)
            meta["regime"] = d_meta["regime"]
            meta["regime_inputs"] = d_meta["regime_inputs"]
            meta["component_weights"] = d_meta["component_weights"]
            meta["data_status"].update(d_meta["data_status"])
        for name, original in protected.items():
            if not out[name].equals(original):
                raise AssertionError(f"Auxiliary transform changed protected column {name}")
        degraded = [f"{k}:{v}" for k, v in meta["data_status"].items() if v != "OBSERVED"]
        meta["overall_data_status"] = "READY" if not degraded else "DEGRADED_" + "|".join(degraded)
        out["aux_data_status"] = meta["overall_data_status"]
        return out, meta

    def _overnight_scores(
        self, ranked: pd.DataFrame, cutoff: pd.Timestamp
    ) -> tuple[pd.Series, dict[str, Any]]:
        cutoff_utc = cutoff.tz_convert("UTC")
        instruments = self.config["overnight_instruments"]
        values: dict[str, float] = {}
        observations: dict[str, Any] = {}
        observed_count = 0
        for instrument, spec in instruments.items():
            rows = self.overnight[self.overnight["instrument"] == instrument] if len(self.overnight) else pd.DataFrame()
            rows = rows[rows["available_at"] <= cutoff_utc] if len(rows) else rows
            if not len(rows):
                values[instrument] = 0.0
                observations[instrument] = {"status": "UNKNOWN", "reason": self._source_reason(instrument)}
                continue
            row = rows.sort_values(["available_at", "session_date"]).iloc[-1]
            age_hours = (cutoff_utc - row.available_at).total_seconds() / 3600.0
            if age_hours > float(spec["max_age_hours"]):
                values[instrument] = 0.0
                observations[instrument] = {
                    "status": "STALE",
                    "available_at": row.available_at.isoformat(),
                    "age_hours": age_hours,
                    "source_sha256": row.source_sha256,
                }
                continue
            scaled = float(row.return1) / float(spec["scale_return"])
            value = float(np.clip(scaled, -1.0, 1.0) * float(spec["direction"]))
            values[instrument] = value
            observed_count += 1
            observations[instrument] = {
                "status": "OBSERVED",
                "session_date": str(row.session_date.date()),
                "available_at": row.available_at.isoformat(),
                "return1": float(row.return1),
                "normalized": value,
                "source_url": row.source_url,
                "source_sha256": row.source_sha256,
            }
        categories = ranked[self.config["sector_id_column"]].map(self._sector_categories) \
            if self.config["sector_id_column"] in ranked else pd.Series([{"all"}] * len(ranked), index=ranked.index)
        scores = []
        for cats in categories:
            numerator = denominator = 0.0
            for instrument, spec in instruments.items():
                weights = spec.get("weights", {})
                applicable = sum(float(weight) for key, weight in weights.items() if key == "all" or key in cats)
                numerator += applicable * values[instrument]
                denominator += abs(applicable)
            scores.append(numerator / denominator if denominator else 0.0)
        status = "OBSERVED" if observed_count == len(instruments) else (
            "UNKNOWN" if observed_count == 0 else "PARTIAL"
        )
        return pd.Series(scores, index=ranked.index, dtype=float), {"status": status, "observations": observations}

    def _source_reason(self, instrument: str) -> str:
        sources = self.source_status.get("sources", {}) if isinstance(self.source_status, dict) else {}
        record = sources.get(instrument, {})
        return record.get("reason") or record.get("status") or "NO_ELIGIBLE_POINT_IN_TIME_ROW"

    def _sector_categories(self, value: Any) -> set[str]:
        raw = str(value).strip()
        token = raw.replace(":", "_").split("_")[-1]
        cats = {"all"}
        tech_ids = {str(x) for x in self.config["technology_sector_ids"]}
        semi_ids = {str(x) for x in self.config["semiconductor_sector_ids"]}
        upper = raw.upper()
        if token in tech_ids or any(x in upper for x in ("電子", "半導體", "TECH", "ELECTRON")):
            cats.add("technology")
        if token in semi_ids or "半導體" in upper or "SEMICONDUCT" in upper:
            cats.update(("technology", "semiconductor"))
        return cats

    def _apply_d(self, frame: pd.DataFrame, cutoff: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, Any]]:
        neutral = float(self.config["neutral_component"])
        base = _clip01(frame[self.config["base_score_column"]].astype(float))
        sector_col = self.config["sector_score_column"]
        if sector_col in frame:
            raw_sector = pd.Series(_clip01(frame[sector_col].astype(float)), index=frame.index)
            if "sector_status" in frame:
                row_status = frame["sector_status"].fillna("UNKNOWN").astype(str).str.upper()
                available = row_status.eq("AVAILABLE") | row_status.eq("OBSERVED")
                stale = row_status.str.startswith("STALE")
                usable = available | stale
                sector = raw_sector.where(usable, neutral)
                if available.all():
                    sector_status = "OBSERVED"
                elif not usable.any():
                    sector_status = "UNKNOWN"
                elif stale.any() and usable.all():
                    sector_status = "STALE"
                else:
                    sector_status = "PARTIAL"
            else:
                sector = raw_sector
                sector_status = "OBSERVED"
        else:
            sector = pd.Series(neutral, index=frame.index)
            sector_status = "UNKNOWN"
        overnight = pd.Series(_clip01((frame["overnight_score"].astype(float) + 1.0) / 2.0), index=frame.index)
        earnings, earnings_events, earnings_status = self._pit_scores("earnings", self.earnings, frame, cutoff)
        institutional, _, institutional_status = self._pit_scores("institutional", self.institutional, frame, cutoff)
        risk = self._risk_score(frame)
        regime, regime_inputs = self._classify_regime(frame, earnings_events)
        weights = self.config["regime_weights"][regime]
        components = {
            "price": base,
            "sector": sector,
            "overnight": overnight,
            "earnings": earnings,
            "institutional": institutional,
            "risk": risk,
        }
        total = sum(float(weights[name]) * component for name, component in components.items())
        frame["price_component"] = base
        frame["sector_component"] = sector
        frame["overnight_component"] = overnight
        frame["earnings_component"] = earnings
        frame["institutional_component"] = institutional
        frame["risk_component"] = risk
        frame["regime"] = regime
        frame["d_score"] = total
        frame[self.config["score_column"]] = frame["d_score"]
        return frame, {
            "regime": regime,
            "regime_inputs": regime_inputs,
            "component_weights": weights,
            "data_status": {
                "sector": sector_status,
                "earnings": earnings_status,
                "institutional": institutional_status,
                "risk": "OBSERVED" if "volatility_ratio" in frame else "PARTIAL",
            },
        }

    def _pit_scores(
        self, name: str, table: pd.DataFrame, frame: pd.DataFrame, cutoff: pd.Timestamp
    ) -> tuple[pd.Series, pd.Series, str]:
        neutral = float(self.config["neutral_component"])
        score = pd.Series(neutral, index=frame.index, dtype=float)
        events = pd.Series(False, index=frame.index, dtype=bool)
        if not len(table):
            return score, events, "UNKNOWN"
        eligible = table[table["available_at"] <= cutoff.tz_convert("UTC")].copy()
        if not len(eligible):
            return score, events, "UNKNOWN"
        eligible = eligible.sort_values(["symbol", "available_at"]).groupby("symbol", as_index=False).tail(1)
        age = (cutoff.tz_convert("UTC") - eligible["available_at"]).dt.total_seconds() / 86400.0
        eligible = eligible[age <= float(self.config["pit_max_age_days"][name])]
        lookup = eligible.set_index("symbol")
        observed = 0
        for idx, symbol in frame["symbol"].astype(str).items():
            if symbol not in lookup.index:
                continue
            row = lookup.loc[symbol]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            score.at[idx] = float(np.clip((float(row["score"]) + 1.0) / 2.0, 0.0, 1.0))
            observed += 1
            if "event_flag" in row:
                events.at[idx] = bool(row["event_flag"])
        status = "OBSERVED" if observed == len(score) else ("UNKNOWN" if observed == 0 else "PARTIAL")
        return score, events, status

    def _risk_score(self, frame: pd.DataFrame) -> pd.Series:
        cfg = self.config["risk_transform"]
        neutral = float(self.config["neutral_component"])
        if "volatility_ratio" in frame:
            vol = pd.to_numeric(frame["volatility_ratio"], errors="coerce")
            vol_score = 1.0 - (vol - cfg["volatility_low"]) / (cfg["volatility_high"] - cfg["volatility_low"])
            vol_score = pd.Series(_clip01(vol_score), index=frame.index).fillna(neutral)
        else:
            vol_score = pd.Series(neutral, index=frame.index)
        if "return1" in frame:
            ret = pd.to_numeric(frame["return1"], errors="coerce")
            chase_score = pd.Series(np.where(ret > cfg["one_day_chase_return"], 0.0, 1.0), index=frame.index)
            chase_score[ret.isna()] = neutral
        else:
            chase_score = pd.Series(neutral, index=frame.index)
        return cfg["volatility_weight"] * vol_score + cfg["chase_weight"] * chase_score

    def _classify_regime(self, frame: pd.DataFrame, event_flags: pd.Series) -> tuple[str, dict[str, float | None]]:
        def median(column: str) -> float | None:
            if column not in frame:
                return None
            values = pd.to_numeric(frame[column], errors="coerce").dropna()
            return float(values.median()) if len(values) else None

        r20, r1, vol = median("return20"), median("return1"), median("volatility_ratio")
        if "trend" in frame:
            trend_fraction = float(pd.to_numeric(frame["trend"], errors="coerce").fillna(0).gt(0).mean())
        elif {"signal_price", "ema20", "ema50"} <= set(frame):
            trend_fraction = float(((frame.signal_price > frame.ema20) & (frame.ema20 > frame.ema50)).mean())
        else:
            trend_fraction = None
        overnight_market = float(frame["overnight_score"].median()) if len(frame) else 0.0
        event_fraction = float(event_flags.mean()) if len(event_flags) else 0.0
        t = self.config["regime_thresholds"]
        if event_fraction >= t["event_fraction"]:
            regime = "EVENT"
        elif r20 is not None and r1 is not None and r20 <= t["panic_return20"] and r1 >= t["rebound_return1"]:
            regime = "PANIC_REBOUND"
        elif (vol is not None and vol >= t["high_volatility_ratio"]) or abs(overnight_market) >= t["high_overnight_abs"]:
            regime = "HIGH_VOL"
        elif (r20 is not None and trend_fraction is not None and vol is not None
              and r20 >= t["bull_return20"] and trend_fraction >= t["bull_trend_fraction"]
              and vol <= t["bull_max_volatility_ratio"]):
            regime = "BULL_TREND"
        else:
            regime = "RANGE"
        return regime, {
            "median_return20": r20,
            "median_return1": r1,
            "median_volatility_ratio": vol,
            "trend_fraction": trend_fraction,
            "overnight_market_score": overnight_market,
            "event_fraction": event_fraction,
        }


def transform(
    day: Any,
    ranked: pd.DataFrame,
    *,
    version: str,
    calendar_path: str | Path,
    auxiliary_dir: str | Path = DEFAULT_AUXILIARY,
    config_path: str | Path = DEFAULT_CONFIG,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Stateless convenience wrapper; reuse ``AuxiliarySignals`` in loops."""
    return AuxiliarySignals(calendar_path, auxiliary_dir, config_path).modify(version, day, ranked)
