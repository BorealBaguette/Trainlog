"""Aircraft families for the aircraft types page.

Each family lists its ICAO type codes (as stored in the `airliners` table);
the first one's picture stands for the family (unless it has a generic code,
below) until the user has flown one of them. The page sorts the variants by
name itself. Families follow generations
rather than type certificates, so a 737-800 ticks off the 737 NG but not the
737 MAX.

Every row of `airliners` appears on the page. A code missing from here (say a
type an admin adds later) is shown as a family of its own, named after its
manufacturer and model, until it is added below.

ALIASES are codes that clash with a specific model (same aircraft, two codes):
their flights count towards that model and they get no entry of their own.

GENERICS are codes for a whole family rather than one model. They are not
listed as a variant; they picture the family, and flights logged with them
still count as flying the family.
"""

ALIASES = {
    "B73X": "B737",  # 737-700
    "A330": "A333",  # A330-300
    "B787": "B788",  # 787-8
    "E175": "E75S",  # E175 (Short Wing)
}

GENERICS = {
    "BCSX": "Airbus A220",
    "A340": "Airbus A340",
    "A350": "Airbus A350",
    "A380": "Airbus A380",
    "B717": "Boeing 717",
    "B757": "Boeing 757",
    "B767": "Boeing 767",
    "B777": "Boeing 777",
}

AIRCRAFT_FAMILIES = [
    ("Airbus A220", ["BCS3", "BCS1"]),
    ("Airbus A300", ["A306"]),
    ("Airbus A320 family", ["A320", "A318", "A319", "A321"]),
    ("Airbus A320neo family", ["A20N", "A19N", "A21N"]),
    ("Airbus A330", ["A333", "A332"]),
    ("Airbus A330neo", ["A339", "A338"]),
    ("Airbus A340", ["A343", "A342", "A345", "A346"]),
    ("Airbus A350", ["A359", "A35K"]),
    ("Airbus A380", ["A388"]),
    ("ATR 42", ["AT46", "AT43", "AT44", "AT45"]),
    ("ATR 72", ["AT76", "AT75"]),
    ("Boeing 707", ["B703"]),
    ("Boeing 717", ["B712"]),
    ("Boeing 727", ["B722"]),
    ("Boeing 737 Original", ["B732", "B731"]),
    ("Boeing 737 Classic", ["B733", "B734", "B735"]),
    ("Boeing 737 NG", ["B738", "B736", "B737", "B739"]),
    ("Boeing 737 MAX", ["B38M", "B37M", "B39M", "B3XM"]),
    ("Boeing 747", ["B744", "B741", "B742", "B743", "B748"]),
    ("Boeing 757", ["B752", "B753"]),
    ("Boeing 767", ["B763", "B762", "B764"]),
    ("Boeing 777", ["B77W", "B772", "B77L", "B773"]),
    ("Boeing 777X", ["B779", "B778"]),
    ("Boeing 787", ["B789", "B788", "B78X"]),
    ("BAe 146 / Avro RJ", ["RJ85", "B461"]),
    ("De Havilland Canada Dash 8", ["DH8D", "DH8A", "DH8B", "DH8C"]),
    ("Embraer E-Jet", ["E190", "E170", "E75L", "E75S", "E195"]),
    ("Embraer E-Jet E2", ["E295", "E275", "E290"]),
    ("Embraer ERJ", ["E145", "E135", "E140"]),
    ("Fokker 50", ["F50"]),
    ("Fokker 70/100", ["F100", "F70"]),
    ("McDonnell Douglas DC-9", ["DC93"]),
    ("McDonnell Douglas MD-80", ["MD82", "MD83", "MD87", "MD88"]),
    ("Mitsubishi CRJ", ["CRJ9", "CRJ2", "CRJ7", "CRJX"]),
]

# Families listed in an order of their own rather than alphabetically: the
# page sorts them by this text instead of their name.
SORT_AS = {
    "Boeing 737 Original": "Boeing 737 1",
    "Boeing 737 Classic": "Boeing 737 2",
    "Boeing 737 NG": "Boeing 737 3",
    "Boeing 737 MAX": "Boeing 737 4",
}

# code -> (family name, position within the family's list)
FAMILY_OF = {
    code: (name, index)
    for name, codes in AIRCRAFT_FAMILIES
    for index, code in enumerate(codes)
}
