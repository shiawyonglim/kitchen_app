"""Weekly background script to refresh liquid asset values via AI Web Search."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.modules.assets import service as asset_service  # noqa: E402


def main() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        asset_service.seed_default_assets(db)
        assets = asset_service.refresh_all_assets(db)
        net = asset_service.get_net_worth(db)

    print("=== Weekly AI Web Search Asset Sync Complete ===")
    for asset in assets:
        val = asset.latest_valuation
        if val:
            print(
                f"- {asset.name}: RM {val.median_price_myr:,.2f} "
                f"(range RM {val.min_price_myr:,.2f} - RM {val.max_price_myr:,.2f}, "
                f"n={val.sample_count}, source={val.source})"
            )
    print(f"Total Tracked Assets Value: RM {net.tracked_assets_value_myr:,.2f}")
    print(f"Total Net Worth (Cash + Assets): RM {net.total_net_worth_myr:,.2f}")


if __name__ == "__main__":
    main()

