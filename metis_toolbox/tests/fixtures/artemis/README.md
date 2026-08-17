# Artemis test fixtures — captured-real miniatures

All four files are byte-level excerpts of real public-domain Census Bureau
shapefiles (records copied verbatim; only the .shp header length/bbox, record
numbers, and .dbf record count were rewritten), per the §10 captured-real rule.

- `roads_mini.shp` / `roads_mini.dbf` — 12 records from
  `TIGER2025/ROADS/tl_2025_54033_roads.zip` (Harrison County WV), chosen to
  span eight MTFCC classes (S1100, S1200, S1400, S1500, S1630, S1730, S1740,
  S1820) with named and unnamed roads.
- `water_line_mini.shp` / `water_line_mini.dbf` — 10 records from
  `TIGER2025/LINEARWATER/tl_2025_54033_linearwater.zip`: six named runs and
  creeks plus four unnamed drainage lines, so the name-driven tier rule is
  exercised in both directions.
- `water_area_mini.shp` / `water_area_mini.dbf` — 9 polygons from
  `TIGER2025/AREAWATER/tl_2025_54033_areawater.zip`: seven named lakes, the
  county's lone H2040 reservoir, and two unnamed ponds.
- `counties_mini.shp` / `counties_mini.dbf` — 15 county polygons from
  `GENZ2025/shp/cb_2025_us_county_500k.zip`: Harrison County WV and every
  neighbour whose bbox comes within ~0.35°, including two Pennsylvania
  counties so cross-state FIPS resolution is exercised.

Captured 2026-08-09 with a one-off scratch script; re-capture only if the
TIGER vintage the code targets changes.
