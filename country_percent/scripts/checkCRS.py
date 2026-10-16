import json
from collections import defaultdict
from pathlib import Path

groups = defaultdict(list)

for path in sorted(Path("countries/processed").glob("GB*.geojson")):
    with path.open() as f:
        data = json.load(f)

    crs = data.get("crs", {}).get("properties", {}).get("name", "MISSING")
    groups[crs].append(path.name)

for crs, files in groups.items():
    print(f"\nCRS: {crs} ({len(files)} files)")
    for filename in files:
        print(f"  {filename}")
