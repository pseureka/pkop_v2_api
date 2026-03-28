"""
Coordinate conversion helpers.

JSON source:  [[lng, lat], ...]   (GeoJSON order)
PostGIS WKT:  POLYGON((lng lat, ...))
Frontend:     [[lat, lng], ...]   (Leaflet order)
"""


def json_polygon_to_wkt(coords: list[list[float]]) -> str:
    """[[lng,lat],...] → WKT POLYGON((lng lat,...)) — closes ring if open"""
    pts = list(coords)
    # Close the ring if needed
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    inner = ", ".join(f"{p[0]} {p[1]}" for p in pts)
    return f"POLYGON(({inner}))"


def wkt_to_frontend_coords(wkt: str) -> list[list[float]]:
    """WKT POLYGON((lng lat,...)) → [[lat,lng],...] open ring for frontend"""
    # Strip POLYGON(( ... ))
    inner = wkt.strip()
    if inner.upper().startswith("POLYGON"):
        inner = inner[inner.index("(") + 1 :]
        inner = inner.strip("()")
    pairs = [p.strip() for p in inner.split(",")]
    coords = []
    for pair in pairs:
        parts = pair.split()
        coords.append([float(parts[1]), float(parts[0])])  # [lat, lng]
    # Drop closing duplicate if present
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords


def frontend_coords_to_wkt(coords: list[list[float]]) -> str:
    """[[lat,lng],...] → WKT POLYGON((lng lat,...)) — frontend sends lat-first"""
    pts = list(coords)
    # Close the ring if needed
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    inner = ", ".join(f"{p[1]} {p[0]}" for p in pts)
    return f"POLYGON(({inner}))"
