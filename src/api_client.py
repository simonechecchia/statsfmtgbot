from functools import cache, wraps
import requests
from datetime import datetime, timedelta
import concurrent.futures
from config import API_BASE_URL, USER_AGENT, ENV
import random
from collections import Counter
from database import get_user_setting
from utils import format_listening_time

import sys, time

sys.stdout.reconfigure(encoding="utf-8")


def time_counter(func):
    def wrapper(*args, **kwargs):
        if ENV == "debug":
            # print(f"Executing {func.__name__}...")
            start_time = time.time()
            result = func(*args, **kwargs)
            end_time = time.time()
            execution_time = end_time - start_time
            print(f"{func.__name__} executed in {execution_time:.4f} seconds")
        else:
            result = func(*args, **kwargs)
        return result

    return wrapper


def timed_cache(seconds=1800):
    def decorator(func):
        cache = {}

        @wraps(func)
        def wrapper(*args, **kwargs):
            key = str(args) + str(kwargs)
            current_time = datetime.now()
            if key in cache:
                result, timestamp = cache[key]
                if current_time - timestamp < timedelta(seconds=seconds):
                    return result
            result = func(*args, **kwargs)
            cache[key] = (result, current_time)
            return result

        return wrapper

    return decorator


@timed_cache()
def api_get(url, **params):
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        if ENV == "debug" or ENV == "development":
            print(f"Error during request: {e}")
        return None


def parallel_execute(func, items, timeout=60):
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_item = {executor.submit(func, item): item for item in items}
        try:
            for future in concurrent.futures.as_completed(
                future_to_item, timeout=timeout
            ):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    if ENV == "debug" or ENV == "development":
                        print(f"Generated an exception: {exc}")
        except concurrent.futures.TimeoutError:
            if ENV == "debug":
                print("Parallel execution timed out")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    return results


@time_counter
def get_complex_recommendations(username, limit=5):
    all_tracks = get_all_items(username, "", "tracks")
    all_albums = get_all_items(username, "", "albums")

    artists = Counter(
        artist["id"] for track in all_tracks for artist in track["track"]["artists"]
    )
    genres = Counter(
        genre for album in all_albums for genre in album["album"]["genres"]
    )

    similar_artists = find_similar_artists(artists)
    unheard_tracks = find_unheard_tracks(
        username, similar_artists, set(track["track"]["id"] for track in all_tracks)
    )
    unheard_albums = find_unheard_albums(
        username, similar_artists, set(album["album"]["id"] for album in all_albums)
    )

    scored_tracks = score_recommendations(unheard_tracks, genres)
    scored_albums = score_recommendations(unheard_albums, genres, type="albums")

    recommended_tracks = select_diverse_recommendations(scored_tracks, limit)
    recommended_albums = select_diverse_recommendations(scored_albums, limit)

    return recommended_tracks, recommended_albums


@time_counter
def find_similar_artists(artists):
    artist_list = list(artists.keys())
    # Optimized: Reduce the number of artists we query to balance speed and quality
    random_artists = random.sample(artist_list, min(15, len(artist_list)))
    top_artists = [artist for artist, _ in artists.most_common(15)]
    combined_artists = list(set(top_artists + random_artists))

    related_artists = set(
        artist
        for sublist in parallel_execute(get_related_artists, combined_artists)
        for artist in sublist
    )
    top_artists_set = set(dict(artists.most_common(250)).keys())
    return list(related_artists - top_artists_set)


@time_counter
@timed_cache(seconds=14400)
def get_related_artists(artist_id):
    data = api_get(f"{API_BASE_URL}/artists/{artist_id}/related")
    if data:
        artists = [artist["id"] for artist in data["items"]]
        return random.sample(artists, min(5, len(artists)))
    return []


@time_counter
@timed_cache(seconds=7200)
def get_artist_top_tracks(artist_id):
    data = api_get(f"{API_BASE_URL}/artists/{artist_id}/tracks")
    if data:
        # Optimized: Reduce the number of tracks returned to improve performance
        # Filter tracks with duration >= 60 seconds (60000 ms), skip if duration missing
        filtered_data = [item for item in data["items"] if item.get("durationMs") and item["durationMs"] >= 60000]
        return filtered_data[: min(20, len(filtered_data))]
    return []


@time_counter
def find_unheard_tracks(username, artists, user_tracks):
    # Optimized: Reduce number of artists queried for better performance
    sample_size = min(8, len(artists))
    all_top_tracks = parallel_execute(get_artist_top_tracks, random.sample(artists, sample_size))
    return [
        track
        for artist_tracks in all_top_tracks
        for track in artist_tracks
        if track["id"] not in user_tracks
    ]


@time_counter
def find_unheard_albums(username, artists, user_albums):
    # Optimized: Reduce number of artists queried for better performance
    sample_size = min(8, len(artists))
    all_albums = parallel_execute(get_artist_albums, random.sample(artists, sample_size))
    return [
        album
        for artist_albums in all_albums
        for album in artist_albums
        if album["id"] not in user_albums and album.get("type") == "album"
    ]


@time_counter
@timed_cache(seconds=7200)
def get_artist_albums(artist_id):
    data = api_get(f"{API_BASE_URL}/artists/{artist_id}/albums")
    return data["items"] if data else []


@time_counter
def score_recommendations(items, user_genres, type="tracks"):
    total_genre_count = sum(user_genres.values())
    
    # Optimized: Avoid division by zero and reduce API calls
    if total_genre_count == 0:
        # If no genre data, return items with random scores
        return [(item, random.uniform(0, 1)) for item in items if item is not None]

    def score_item(item):
        # Score items based on genre match with user preferences
        try:
            item_genres = (
                get_album(item["albums"][0]["id"])["item"]["genres"]
                if type == "tracks" and item.get("albums") and len(item["albums"]) > 0
                else item.get("genres", [])
            )
            score = sum(
                user_genres.get(genre, 0) / total_genre_count for genre in item_genres
            )
            score += random.uniform(0, 0.2)
            return item, score
        except (KeyError, IndexError, TypeError):
            # If we can't get genre info, give it a low random score
            return item, random.uniform(0, 0.1)

    scored_items = parallel_execute(score_item, items)
    # Filter out any None items returned by parallel_execute
    return sorted(
        [item for item in scored_items if item and item[0] is not None],
        key=lambda x: x[1],
        reverse=True,
    )


@time_counter
def select_diverse_recommendations(scored_items, limit):
    selected = []
    artists_selected = set()

    for item, _ in scored_items:
        artist_id = (
            item["artists"][0]["id"] if "artists" in item else item["artist"]["id"]
        )
        if artist_id not in artists_selected:
            selected.append(item)
            artists_selected.add(artist_id)
        if len(selected) == limit:
            break

    return selected


@timed_cache(seconds=120)
def get_all_items(username, period, item_type):
    all_items = []
    offset = 0
    limit = 500

    while True:
        data = api_get(
            f"{API_BASE_URL}/users/{username}/top/{item_type}",
            limit=limit,
            offset=offset,
            range=period,
        )
        if not data or not data["items"]:
            break
        all_items.extend(data["items"])
        if item_type == "genres":
            break
        offset += limit

    return all_items

@time_counter
def get_user_now(username):
    data = api_get(f"{API_BASE_URL}/users/{username}/streams/current")
    return data["item"] if data else None

@time_counter
def format_item_info(item, item_type, username):
    if item_type == "tracks":
        # Optimized: Avoid unnecessary API calls by defaulting to "N/A" instead of fetching
        artist_name = "N/A"
        artist_id = "N/A"
        if item["track"].get("artists") and len(item["track"]["artists"]) > 0:
            artist_name = item["track"]["artists"][0]["name"]
            artist_id = item["track"]["artists"][0]["id"]
        
        return {
            "position": item["position"],
            "title": item["track"]["name"],
            "artist": artist_name,
            "album": (
                item["track"]["albums"][0]["name"] if item["track"].get("albums") else "N/A"
            ),
            "streams": item["streams"],
            "duration_ms": item["track"].get("durationMs", 0),
            "total_listening_time_ms": item.get("playedMs", 0),
            "spotify_popularity": (
                item["track"]["spotifyPopularity"]
                if item["track"].get("spotifyPopularity")
                else "N/A"
            ),
            "stats_id": item["track"]["id"],
            "artist_id": artist_id,
            "spotify_id": (
                item["track"]["externalIds"]["spotify"][0]
                if item["track"].get("externalIds", {}).get("spotify")
                and item["track"]["externalIds"]["spotify"]
                and len(item["track"]["externalIds"]["spotify"]) > 0
                else "N/A"
            ),
            "preview_url": item["track"].get("spotifyPreview")
            or item["track"].get("appleMusicPreview")
            or "N/A",
            "album_image": (
                item["track"]["albums"][0]["image"]
                if item["track"].get("albums")
                else "N/A"
            ),
        }
    elif item_type == "albums":
        # Optimized: Avoid unnecessary API calls by defaulting to "N/A" instead of fetching
        artist_name = "N/A"
        artist_id = "N/A"
        if item["album"].get("artists") and len(item["album"]["artists"]) > 0 and item["album"]["artists"][0]:
            artist_name = item["album"]["artists"][0]["name"]
            artist_id = item["album"]["artists"][0]["id"]
        
        return {
            "position": item["position"],
            "title": item["album"]["name"],
            "artist": artist_name,
            "streams": item["streams"],
            "stats_id": item["album"]["id"],
            "artist_id": artist_id,
            "image": item["album"]["image"] if item["album"].get("image") else "N/A",
            "date": datetime.fromtimestamp(item["album"]["releaseDate"] / 1000).date() if item["album"].get("releaseDate") else "N/A",
        }
    elif item_type == "artists":
        return {
            "position": item["position"],
            "name": item["artist"]["name"],
            "streams": item["streams"],
            "stats_id": item["artist"]["id"],
            "image": item["artist"] if item["artist"].get("image") else "N/A",
        }
    elif item_type == "genres":
        return {
            "position": item["position"],
            "name": item["genre"]["tag"],
            "streams": item["streams"],
        }


@time_counter
def get_artist_name(username, album_id):
    try:
        items = get_album_items(username, album_id)
        artist_id = items[0]["artistIds"][0]
        base_url = f"{API_BASE_URL}/artists/{artist_id}"
        data = api_get(base_url)
        return data["item"]["name"]
    except Exception as e:
        if ENV == "debug":
            print(f"Error getting artist name: {e}")
        return "N/A"


@time_counter
def get_album_items(username, album_id):
    base_url = f"{API_BASE_URL}/users/{username}/streams/albums/{album_id}"
    data = api_get(base_url)
    return data["items"]


@time_counter
@timed_cache(seconds=3600)  # Cache for 1 hour since first listen rarely changes
def get_first_last_listen(username, item_type, item_id, first_or_last):
    base_url = f"{API_BASE_URL}/users/{username}/streams/{item_type}/{item_id}"
    params = {"limit": 1, "order": "asc" if first_or_last == "first" else "desc"}
    data = api_get(base_url, limit=params["limit"], order=params["order"])
    if data and data.get("items"):
        return datetime.strptime(
            data["items"][0]["endTime"], "%Y-%m-%dT%H:%M:%S.%fZ"
        ).strftime("%d/%m/%Y")
    return "N/A"


@time_counter
def get_total_listening_time(username, item_type, item_id):
    # TODO: This doesn't work properly: loop through all pages

    data = api_get(f"{API_BASE_URL}/users/{username}/streams/{item_type}/{item_id}")
    total_ms = sum(item["playedMs"] for item in data["items"])
    return format_listening_time(total_ms)


@time_counter
@timed_cache(seconds=7200)
def get_album(album_id):
    return api_get(f"{API_BASE_URL}/albums/{album_id}")

@time_counter
def get_group_usernames(bot, chat_id):
    """
    Get usernames from group members.
    Note: This function has limited functionality as the Telegram Bot API
    doesn't provide a direct way to list all group members without admin privileges.
    Returns empty list for now as a safe default.
    
    To implement this properly would require:
    1. Bot to be admin in the group
    2. Manual tracking of user interactions
    3. Using getChatAdministrators API call (limited to admins only)
    """
    try:
        # The pyTelegramBotAPI doesn't provide bot.get_chat_members()
        # Return empty list to prevent errors until proper implementation
        return []
    
    except Exception as e:
        print(f"Error retrieving usernames: {e}")
        return []