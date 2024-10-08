import requests
import time
import sys
import pymysql
import datetime
sys.path.insert(0, './common')
from utils import connect_to_db
gap = 4


def price_feed(type):
    print('Fetching historical price feeds...')
    url = 'https://web-api.coinmarketcap.com/v1/cryptocurrency/ohlcv/historical'
    querystring = {
        "id": "1966",
        "convert": type,
        "time_start": "1311100800",
        "time_end": int(time.time())
    }
    data = requests.request("GET", url, params=querystring).json()
    formatted_dict = {}
    for quote in data["data"]["quotes"]:
        formatted_dict[quote["time_close"][:10]] = quote["quote"][type]["close"]
    last_quote = data["data"]["quotes"][-1]
    current_date = str(datetime.datetime.strptime(last_quote["time_close"][:10], "%Y-%m-%d") + datetime.timedelta(days=1))[:10]
    formatted_dict[current_date] = last_quote["quote"][type]["close"]
    return formatted_dict


def import_events(conn, querystring, mana_eth_dict, mana_usd_dict):

    print('Start api requests...')
    url = "https://api.opensea.io/api/v1/events"
    events = requests.request("GET", url, params=querystring).json()

    rows = []
    for event in events["asset_events"]:
        if event["asset"] is None or event["event_type"] not in ['created', 'successful']:
            continue
        asset_url = "https://api.opensea.io/api/v1/assets"
        querystring2 = {
            "token_ids": event["asset"]["token_id"],
            "asset_contract_address": event["asset"]["asset_contract"]["address"]
        }
        assets = requests.request("GET", asset_url, params=querystring2).json()
        asset = assets["assets"][0]


        price_mana = int(event["starting_price"])/1e18 if event["event_type"] == "created" else int(event["total_price"])/1e18
        land_type = [x for x in asset["traits"] if x["trait_type"] == "Type"][0]["value"]
        land_size = [x for x in asset["traits"] if x["trait_type"] == "Size"][0]["value"] if land_type == "Estate" else 1
        if land_size == 0:
            continue
        event_timestamp = event["created_date"]
        price_usd = price_mana * mana_usd_dict[event_timestamp[:10]]
        price_eth = price_mana * mana_eth_dict[event_timestamp[:10]]
        distance_to_road_trait = [x for x in asset["traits"] if x["trait_type"] == "Distance to Road"]
        distance_to_road = distance_to_road_trait[0]["value"] if len(distance_to_road_trait) > 0 else None
        distance_to_district_trait = [x for x in asset["traits"] if x["trait_type"] == "Distance to District"]
        distance_to_district = distance_to_district_trait[0]["value"] if len(distance_to_district_trait) > 0 else None
        distance_to_plaza_trait = [x for x in asset["traits"] if x["trait_type"] == "Distance to Plaza"]
        distance_to_plaza = distance_to_plaza_trait[0]["value"] if len(distance_to_plaza_trait) > 0 else None

        row = {
            "event_id": event["id"],
            "event_timestamp": event_timestamp,
            "land_id": event["asset"]["token_id"],
            "event_type": event["event_type"],
            "land_size": land_size,
            "price_usd": round(price_usd),
            "price_usd_parcel": round(price_usd/land_size),
            "price_eth": round(price_eth, 3),
            "price_eth_parcel": round(price_eth/land_size, 3),
            "price_mana": round(price_mana),
            "price_mana_parcel": round(price_mana/land_size),
            "land_type": land_type,
            "distance_to_road": distance_to_road,
            "distance_to_district": distance_to_district,
            "distance_to_plaza": distance_to_plaza,
            "platform": event["collection_slug"],
            "external_url": event["asset"]["external_link"],
            "opensea_url": event["asset"]["permalink"],
            "seller_address": event["seller"]["address"],
            "buyer_address": event["winner_account"]["address"] if event["winner_account"] else None,
        }
        rows.append(row)
        print("Imported:", event["id"])
        time.sleep(gap)

    print("Start inserting events...")
    with conn.cursor() as cur:
        now_timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        for row in rows:
            sql = f"""
            INSERT INTO land_events(
                event_id,
                event_timestamp,
                land_id,
                event_type,
                land_size,
                price_usd,
                price_usd_parcel,
                price_eth,
                price_eth_parcel,
                price_mana,
                price_mana_parcel,
                land_type,
                distance_to_road,
                distance_to_district,
                distance_to_plaza,
                platform,
                external_url,
                opensea_url,
                seller_address,
                buyer_address,
                updated_timestamp
            ) VALUES (
                "{row["event_id"]}",
                "{row["event_timestamp"]}",
                "{row["land_id"]}",
                "{row["event_type"]}",
                "{row["land_size"]}",
                "{row["price_usd"]}",
                "{row["price_usd_parcel"]}",
                "{row["price_eth"]}",
                "{row["price_eth_parcel"]}",
                "{row["price_mana"]}",
                "{row["price_mana_parcel"]}",
                "{row["land_type"]}",
                {row['distance_to_road'] if row['distance_to_road'] is not None else 'NULL'},
                {row['distance_to_district'] if row['distance_to_district'] is not None else 'NULL'},
                {row['distance_to_plaza'] if row['distance_to_plaza'] is not None else 'NULL'},
                "{row["platform"]}",
                "{row["external_url"]}",
                "{row["opensea_url"]}",
                "{row["seller_address"]}",
                {f"'{row['buyer_address']}'" if row['buyer_address'] is not None else 'NULL'},
                "{now_timestamp}"
            )
            ON DUPLICATE KEY UPDATE
                updated_timestamp = "{now_timestamp}"
            """
            cur.execute(sql)

    # persist data
    conn.commit()
    print("Successfully inserted sales => ", len(rows))


def lambda_handler(event, context):
    conn = connect_to_db()
    jump = 6*3600 #6hours
    start_time = event['start_time'] if 'start_time' in event else int(time.time()) #now
    slots = event['slots'] if 'slots' in event else 5 #now
    timeslots = []
    for i in range(0, slots):
        timeslots.append([start_time - jump*(i+1), start_time - jump*i])

    mana_eth_dict = price_feed("ETH")
    mana_usd_dict = price_feed("USD")

    for timeslot in timeslots:
        print('Datetime:', datetime.datetime.utcfromtimestamp(timeslot[1]).strftime('%Y-%m-%d %H:%M:%S'))
        print('Timeslot: ', timeslot)
        querystring = {
            "only_opensea": "false",
            "offset": "0",
            "collection_slug": "decentraland",
            "occurred_before": timeslot[1],
            "occurred_after": timeslot[0]
        }
        import_events(conn, querystring, mana_eth_dict, mana_usd_dict)
        time.sleep(gap)

    conn.close()


# event = { 'start_time': 1591175681, 'slots': 300 }
# lambda_handler({}, {})
