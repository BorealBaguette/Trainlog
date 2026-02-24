import json


def getStatsGeneral(cursor, query, username, statName, tripType, year=None):
    result = cursor.execute(
        query, {"username": username, "tripType": tripType, "year": year}
    ).fetchall()
    stats = []
    for stat in result:
        if stat[statName]:
            stats.append(dict(stat))
    return stats


def getPodiumizedStats(cursor, query, username, statName, tripType, year=None):
    rawStats = getStatsGeneral(cursor, query, username, statName, tripType, year)
    for index, stat in enumerate(rawStats):
        rawStats[index]["height"] = len(rawStats) - index

    stats = []
    if len(rawStats) == 3:
        stats.append(rawStats[1])
        stats.append(rawStats[0])
        stats.append(rawStats[2])
    return stats


def getStatsCountries(cursor, query, username, km, tripType, year=None):
    result = cursor.execute(
        query, {"username": username, "tripType": tripType, "year": year}
    ).fetchall()
    countries = {}

    for countryList in result:
        countryDict = json.loads(countryList[0])

        for country in countryDict:
            if country not in countries.keys():
                countries[country] = {}
                countries[country]["total"] = 0
                countries[country]["past"] = 0
                countries[country]["plannedFuture"] = 0

            if isinstance(countryDict[country], dict):
                country_value = sum(countryDict[country].values())
            else:
                country_value = countryDict[country]

            if km:
                countries[country]["total"] += country_value
                if countryList["past"] != 0:
                    countries[country]["past"] += country_value
                elif countryList["plannedFuture"] != 0:
                    countries[country]["plannedFuture"] += country_value
            else:
                countries[country]["total"] += (
                    countryList["past"] + countryList["plannedFuture"]
                )
                if countryList["past"] != 0:
                    countries[country]["past"] += countryList["past"]
                elif countryList["plannedFuture"] != 0:
                    countries[country]["plannedFuture"] += countryList["plannedFuture"]

    countries = dict(
        sorted(
            countries.items(),
            key=lambda item: countries[item[0]]["total"],
            reverse=True,
        )
    )

    countriesList = []
    for country in countries:
        countriesList.append(
            {
                "country": country,
                "past": countries[country]["past"],
                "plannedFuture": countries[country]["plannedFuture"],
            }
        )

    return countriesList


def getStatsYears(cursor, query, username, lang, tripType, year=None):
    years = []
    yearsTemp = {}
    future = None
    result = cursor.execute(
        query, {"username": username, "tripType": tripType, "year": year}
    ).fetchall()

    if len(result) != 0:
        for year in result:
            if year["year"] != "future":
                yearsTemp[int(year["year"])] = {
                    "past": int(year["past"]),
                    "plannedFuture": int(year["plannedFuture"]),
                    "future": int(year["future"]),
                }
            else:
                future = year
                result.remove(year)
        for year in range(int(result[0]["year"]), int(result[-1]["year"]) + 1):
            if year in yearsTemp.keys():
                years.append(
                    {
                        "year": year,
                        "past": yearsTemp[year]["past"],
                        "plannedFuture": yearsTemp[year]["plannedFuture"],
                        "future": yearsTemp[year]["future"],
                    }
                )
            else:
                years.append({"year": year, "past": 0, "plannedFuture": 0, "future": 0})
        if future:
            years.append(
                {
                    "year": lang["future"],
                    "past": 0,
                    "plannedFuture": 0,
                    "future": future["future"],
                }
            )
        return years
    else:
        return ""
