
# SSTAC v1.2 Architecture + Data Schema

## Goal
SSTAC v1.2 upgrades the v1.1 historical/performance-aware planner into a discovery engine that can evolve across nights.

## New engine logic
1. Night-to-night drift control
   - The strip center is no longer fixed strictly at opposition.
   - It drifts nightly using:
     lambda_center = opposition + drift_rate * day_index

2. Soft coverage memory
   - Recent archived fields from the last few nights are loaded from master history.
   - Nearby previously covered regions receive a soft penalty, not a hard reject.

3. Diversity term
   - During selection, fields too close to already chosen fields are penalized.
   - Fields that open RA/ecliptic longitude diversity get a bonus.

4. Two-stage selection
   - Stage 1: core strip selection (~75%)
   - Stage 2: exploration fill (~25%)
   - If one stage lacks sufficient candidates, the engine backfills from the full pool.

5. Novelty bias
   - Fields with little recent coverage get a bonus.
   - Repeatedly covered regions decay in priority.

## Preserved from v1.1
- UI
- archive workflow
- history coverage map
- sky quality integration
- export pipeline
- target ID format
