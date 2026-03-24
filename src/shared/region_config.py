"""Static configuration for all 34 AWS commercial Regions."""
from __future__ import annotations
from .types import RegionConfig

ALL_REGIONS: list[RegionConfig] = [
    # ── High risk (baseline >= 15) ──
    RegionConfig("il-central-1",   "Tel Aviv",     32.1,  34.8,   "IL", 25, "eu-west-1",      ["SEA-ME-WE-5", "EIG"]),
    RegionConfig("me-central-1",   "Dubai",        25.2,  55.3,   "AE", 20, "ap-southeast-1", ["SEA-ME-WE-5", "SEA-ME-WE-6", "AAE-1", "FLAG-FALCON"]),
    RegionConfig("me-south-1",     "Bahrain",      26.2,  50.6,   "BH", 18, "ap-southeast-1", ["AAE-1", "EIG", "FLAG-FALCON"]),
    RegionConfig("af-south-1",     "Cape Town",   -33.9,  18.4,   "ZA", 15, "eu-west-1",      ["Equiano"]),

    # ── Medium risk (baseline 8-14) ──
    RegionConfig("ap-east-1",      "Hong Kong",    22.3,  114.2,  "HK", 12, "ap-southeast-1", ["SJC2", "PLCN"]),
    RegionConfig("ap-east-2",      "Seoul (new)",  37.6,  127.0,  "KR", 10, "ap-northeast-1", []),
    RegionConfig("ap-south-1",     "Mumbai",       19.1,  72.9,   "IN", 10, "ap-southeast-1", ["EIG", "FLAG-FALCON"]),
    RegionConfig("ap-south-2",     "Hyderabad",    17.4,  78.5,   "IN", 10, "ap-southeast-1", []),
    RegionConfig("ap-southeast-3", "Jakarta",      -6.2,  106.8,  "ID", 10, "ap-southeast-1", []),
    RegionConfig("ap-southeast-6", "Bangkok (new)", 13.8, 100.5,  "TH",  9, "ap-southeast-1", []),
    RegionConfig("sa-east-1",      "São Paulo",   -23.5, -46.6,   "BR", 10, "us-east-1",      ["Monet"]),
    RegionConfig("mx-central-1",   "Mexico (new)", 19.4, -99.1,   "MX", 10, "us-east-1",      []),
    RegionConfig("ap-southeast-4", "Melbourne",   -37.8,  145.0,  "AU",  8, "ap-southeast-2", []),
    RegionConfig("ap-southeast-5", "Auckland (new)",-36.9, 174.8, "NZ",  8, "ap-southeast-2", []),
    RegionConfig("ap-southeast-7", "Malaysia (new)", 3.1, 101.7,  "MY",  8, "ap-southeast-1", []),
    RegionConfig("eu-south-1",     "Milan",        45.5,  9.2,    "IT",  8, "eu-central-1",   []),
    RegionConfig("eu-south-2",     "Zaragoza",     41.7, -0.9,    "ES",  8, "eu-west-1",      []),

    # ── Low risk (baseline < 8) ──
    RegionConfig("ap-northeast-1", "Tokyo",        35.7,  139.7,  "JP",  6, "ap-northeast-2", ["Jupiter", "SJC2"]),
    RegionConfig("ap-northeast-3", "Osaka",        34.7,  135.5,  "JP",  6, "ap-southeast-1", ["Jupiter"]),
    RegionConfig("ap-northeast-2", "Seoul",        37.6,  127.0,  "KR",  5, "ap-northeast-1", []),
    RegionConfig("us-west-1",      "N. California", 37.3,-121.9,  "US",  4, "us-west-2",      ["PLCN"]),
    RegionConfig("us-east-1",      "Virginia",     38.9, -77.5,   "US",  3, "us-west-2",      ["MAREA"]),
    RegionConfig("eu-central-1",   "Frankfurt",    50.1,  8.7,    "DE",  3, "eu-west-1",      []),
    RegionConfig("eu-west-2",      "London",       51.5, -0.1,    "GB",  3, "eu-west-1",      ["MAREA"]),
    RegionConfig("eu-west-3",      "Paris",        48.9,  2.3,    "FR",  3, "eu-central-1",   []),
    RegionConfig("eu-north-1",     "Stockholm",    59.3,  18.1,   "SE",  3, "eu-central-1",   []),
    RegionConfig("ap-southeast-2", "Sydney",      -33.9,  151.2,  "AU",  3, "ap-southeast-1", ["Jupiter"]),
    RegionConfig("us-east-2",      "Ohio",         39.9, -82.6,   "US",  2, "us-west-2",      []),
    RegionConfig("us-west-2",      "Oregon",       45.6, -122.3,  "US",  2, "us-east-1",      ["Jupiter"]),
    RegionConfig("ca-central-1",   "Montreal",     45.5, -73.6,   "CA",  2, "us-east-1",      []),
    RegionConfig("ca-west-1",      "Calgary (new)", 51.0,-114.1,  "CA",  2, "us-west-2",      []),
    RegionConfig("eu-central-2",   "Zurich (new)", 47.4,  8.5,    "CH",  2, "eu-central-1",   []),
    RegionConfig("eu-west-1",      "Dublin",       53.3, -6.3,    "IE",  2, "eu-central-1",   []),
    RegionConfig("ap-southeast-1", "Singapore",     1.3,  103.8,  "SG",  2, "ap-southeast-2", ["SEA-ME-WE-5", "SJC2", "Jupiter"]),
]

# Quick lookup maps
REGION_MAP: dict[str, RegionConfig] = {r.code: r for r in ALL_REGIONS}
COUNTRY_TO_REGIONS: dict[str, list[str]] = {}
for _r in ALL_REGIONS:
    COUNTRY_TO_REGIONS.setdefault(_r.country, []).append(_r.code)
