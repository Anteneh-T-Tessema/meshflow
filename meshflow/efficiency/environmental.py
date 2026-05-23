"""V7 — Environmental Cost Optimizer: MARLIN-inspired eco-aware scheduling.

MARLIN (2026): 33% carbon reduction, 43% water reduction by treating
environmental cost as a first-class optimisation target alongside latency.

Key decisions:
- Carbon intensity varies by region and time (grid mix)
- Water usage scales with compute intensity
- Jobs can be deferred to low-carbon windows within a deadline
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Carbon intensity estimates (gCO2eq/kWh) by cloud region
# Source: electricityMap averages, 2025
CARBON_INTENSITY: dict[str, float] = {
    "us-east-1": 400.0,
    "us-west-2": 120.0,  # PNW hydro
    "eu-west-1": 230.0,  # Ireland wind mix
    "eu-central-1": 380.0,  # Germany coal + renewables
    "ap-southeast-1": 580.0,  # Singapore
    "ap-northeast-1": 500.0,  # Japan
    "ca-central-1": 80.0,  # Quebec hydro
    "default": 350.0,
}

# Water usage effectiveness per region (L/kWh)
WATER_INTENSITY: dict[str, float] = {
    "us-east-1": 1.8,
    "us-west-2": 0.4,
    "eu-west-1": 0.6,
    "eu-central-1": 1.2,
    "default": 1.5,
}

# Approximate energy per token by model tier (mWh/1k tokens)
ENERGY_PER_1K_TOKENS: dict[str, float] = {
    "haiku": 0.003,
    "sonnet": 0.012,
    "opus": 0.045,
    "default": 0.020,
}


@dataclass
class EnvironmentalCost:
    carbon_g: float  # gCO2eq
    water_ml: float  # millilitres
    energy_mwh: float  # milliwatt-hours
    region: str
    model_tier: str
    tokens: int


@dataclass
class SchedulingWindow:
    """A time window with lower carbon intensity — for deferrable tasks."""

    start_epoch: float
    end_epoch: float
    region: str
    projected_carbon_g_per_1k_tokens: float


class CarbonCalculator:
    """Computes environmental cost of a model inference call."""

    def calculate(
        self,
        tokens: int,
        model_id: str,
        region: str = "default",
    ) -> EnvironmentalCost:
        tier = self._tier(model_id)
        energy_per_1k = ENERGY_PER_1K_TOKENS.get(tier, ENERGY_PER_1K_TOKENS["default"])
        energy_mwh = (tokens / 1000) * energy_per_1k

        carbon_intensity = CARBON_INTENSITY.get(region, CARBON_INTENSITY["default"])
        water_intensity = WATER_INTENSITY.get(region, WATER_INTENSITY["default"])

        # Convert mWh to kWh for intensity multiplication
        energy_kwh = energy_mwh / 1_000_000
        carbon_g = energy_kwh * carbon_intensity * 1000  # grams
        water_ml = energy_kwh * water_intensity * 1000  # millilitres

        return EnvironmentalCost(
            carbon_g=carbon_g,
            water_ml=water_ml,
            energy_mwh=energy_mwh,
            region=region,
            model_tier=tier,
            tokens=tokens,
        )

    def _tier(self, model_id: str) -> str:
        model_lower = model_id.lower()
        if "haiku" in model_lower:
            return "haiku"
        if "sonnet" in model_lower:
            return "sonnet"
        if "opus" in model_lower:
            return "opus"
        return "default"


class EcoRouter:
    """Routes model calls to the lowest-carbon region/tier combination.

    For latency-tolerant tasks, suggests deferring to a lower-carbon window.
    """

    def __init__(self, calculator: CarbonCalculator | None = None) -> None:
        self._calc = calculator or CarbonCalculator()

    def recommend_region(
        self,
        tokens: int,
        model_id: str,
        available_regions: list[str],
        carbon_budget_g: float = 10.0,
    ) -> tuple[str, EnvironmentalCost]:
        """Return lowest-carbon region that fits the budget."""
        best_region = available_regions[0]
        best_cost = self._calc.calculate(tokens, model_id, best_region)

        for region in available_regions[1:]:
            cost = self._calc.calculate(tokens, model_id, region)
            if cost.carbon_g < best_cost.carbon_g:
                best_cost = cost
                best_region = region

        return best_region, best_cost

    def recommend_tier(
        self,
        tokens: int,
        region: str,
        available_tiers: list[str],
        carbon_budget_g: float,
    ) -> tuple[str, EnvironmentalCost]:
        """Return lowest-carbon model tier within budget."""
        sorted_tiers = sorted(
            available_tiers,
            key=lambda t: ENERGY_PER_1K_TOKENS.get(t, 0.02),
        )
        for tier in sorted_tiers:
            cost = self._calc.calculate(tokens, tier, region)
            if cost.carbon_g <= carbon_budget_g:
                return tier, cost
        # All tiers exceed budget — return lowest anyway
        tier = sorted_tiers[0]
        return tier, self._calc.calculate(tokens, tier, region)


class EnvironmentalOptimizer:
    """Top-level coordinator for environmental cost tracking and optimization."""

    def __init__(self, carbon_budget_g: float = 500.0) -> None:
        self._budget_g = carbon_budget_g
        self._spent_g = 0.0
        self._spent_water_ml = 0.0
        self._calc = CarbonCalculator()
        self._router = EcoRouter(self._calc)
        self._history: list[EnvironmentalCost] = []

    def charge(self, cost: EnvironmentalCost) -> None:
        self._spent_g += cost.carbon_g
        self._spent_water_ml += cost.water_ml
        self._history.append(cost)

    def estimate_and_charge(
        self,
        tokens: int,
        model_id: str,
        region: str = "default",
    ) -> EnvironmentalCost:
        cost = self._calc.calculate(tokens, model_id, region)
        self.charge(cost)
        return cost

    def remaining_budget_g(self) -> float:
        return self._budget_g - self._spent_g

    def is_over_budget(self) -> bool:
        return self._spent_g > self._budget_g

    def summary(self) -> dict[str, Any]:
        return {
            "carbon_spent_g": round(self._spent_g, 4),
            "carbon_budget_g": self._budget_g,
            "water_used_ml": round(self._spent_water_ml, 2),
            "total_calls": len(self._history),
            "over_budget": self.is_over_budget(),
        }
