Natural Earth vector data, release v5.1.2 (https://www.naturalearthdata.com/,
https://github.com/nvkelso/natural-earth-vector). Natural Earth is in the public domain:
"No permission is needed to use Natural Earth. Crediting the authors is unnecessary."
We credit it anyway in the map attribution.

These files are derived: geometry simplified (Douglas-Peucker, 0.004-0.04 deg tolerance),
coordinates rounded to 3 decimals (~100 m), attributes reduced to name/rank, features with
identical attributes merged. They are a locator basemap for orientation only, not survey
data; do not measure against them. Rebuild: dronedog/ui/_build/vendor_assets.py naturalearth.
