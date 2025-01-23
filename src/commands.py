from datetime import datetime
import random
import re
import requests
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    get_user_language,
    get_user_settings,
    set_user_data,
    get_user_setting,
    set_user_setting,
    clear_spotify_token,
    get_user,
)
from language import get_text
from api_client import (
    get_all_items,
    format_item_info,
    get_first_last_listen,
    get_group_usernames,
    get_total_listening_time,
    get_complex_recommendations,
    get_user_now,
)
from spotify_utils import (
    get_spotify_auth_url,
    handle_spotify_callback,
    add_track_to_playlist,
)
from utils import send_long_message
from config import AVAILABLE_SETTINGS, ENV, GITHUB_REPO
import traceback


def register_commands(bot):
    @bot.message_handler(commands=["start"])
    def send_welcome(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)

            if len(message.text.split()) == 1:
                bot.reply_to(message, get_text(lang, "welcome"))
            elif message.text == "/start spotify":
                if get_user_setting(chat_id, "spotify-connected", False):
                    bot.reply_to(message, get_text(lang, "spotify_connected_success"))
                else:
                    bot.reply_to(message, get_text(lang, "spotify_connected_error"))
            else:
                bot.reply_to(message, get_text(lang, "welcome"))

            if get_user(chat_id) is None:
                set_user_data(chat_id, "", "en")
        except Exception as e:
            print(f"Error sending welcome message: {str(e)}")
            if ENV in ["development", "debug"]:
                print(f"Error details: {traceback.format_exc()}")

    @bot.message_handler(commands=["settings"])
    def send_settings(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            bot.send_message(
                chat_id,
                get_text(lang, "settings_message"),
                reply_markup=settings_menu(chat_id, lang),
            )
        except Exception as e:
            print(f"Error sending settings: {str(e)}")

    @bot.message_handler(commands=["connect_spotify"])
    def connect_spotify(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            auth_url = get_spotify_auth_url(chat_id)
            bot.reply_to(
                message, get_text(lang, "spotify_connect_instructions", auth_url)
            )
        except Exception as e:
            print(f"Error connecting to Spotify: {str(e)}")

    @bot.message_handler(commands=["spotify_callback"])
    def spotify_callback(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            code = message.text.split()[1]
            success = handle_spotify_callback(code, chat_id)
            if success:
                set_user_setting(chat_id, "spotify-connected", True)
                bot.reply_to(message, get_text(lang, "spotify_connected_success"))
            else:
                bot.reply_to(message, get_text(lang, "spotify_connected_error"))
        except IndexError:
            bot.reply_to(message, get_text(lang, "spotify_callback_invalid"))
        except Exception as e:
            print(f"Error handling Spotify callback: {str(e)}")
            try:
                bot.reply_to(message, get_text(lang, "spotify_callback_error"))
            except Exception as e:
                print(f"Error sending Spotify callback error: {str(e)}")

    @bot.callback_query_handler(
        func=lambda call: call.data.startswith(("toggle_", "edit_"))
    )
    def handle_setting(call):
        try:
            chat_id = call.message.chat.id
            lang = get_user_language(chat_id)
            action, setting_name = call.data.split("_")

            if setting_name not in AVAILABLE_SETTINGS:
                raise ValueError(f"Not valid setting: {setting_name}")

            setting = AVAILABLE_SETTINGS[setting_name]

            if action == "toggle":
                if setting["type"] == "boolean":
                    current_value = get_user_setting(
                        chat_id, setting_name, setting["default"]
                    )
                    new_value = not current_value
                    set_user_setting(chat_id, setting_name, new_value)
                    bot.answer_callback_query(
                        call.id, get_text(lang, "settings_updated")
                    )
                elif setting["type"] == "callback":
                    if setting_name == "spotify-connected":
                        if get_user_setting(chat_id, "spotify-connected", False):
                            set_user_setting(chat_id, "spotify-connected", False)
                            clear_spotify_token(chat_id)
                            bot.answer_callback_query(
                                call.id, get_text(lang, "spotify_disconnected")
                            )
                        else:
                            connect_spotify(call.message)
                            bot.answer_callback_query(call.id)
                    else:
                        bot.send_message(
                            chat_id, get_text(lang, "setting_not_implemented")
                        )
            elif action == "edit" and setting["type"] == "text":
                bot.answer_callback_query(call.id)
                msg = bot.send_message(
                    chat_id,
                    get_text(lang, "enter_new_value", setting["label"][lang]),
                )
                bot.register_next_step_handler(
                    msg, process_setting_input, setting_name
                )
                return

            new_markup = settings_menu(chat_id, lang)
            try:
                bot.edit_message_reply_markup(
                    chat_id, call.message.message_id, reply_markup=new_markup
                )
            except Exception as e:
                print(f"Error editing message reply markup: {str(e)}")
                try:    
                    bot.edit_message_reply_markup(
                        chat_id,
                    call.message.message_id,
                        reply_markup=call.message.reply_markup,
                    )
                except Exception as e:
                    print(f"Error editing message reply markup: {str(e)}")
        except Exception as e:
            print(f"Error handling setting: {str(e)}")
            try:
                bot.answer_callback_query(call.id, get_text(lang, "error_occurred"))
            except Exception as e:
                print(f"Error answering callback query: {str(e)}")

    def process_setting_input(message, setting_name):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            new_value = message.text.strip()

            set_user_setting(chat_id, setting_name, new_value)
            bot.send_message(
                chat_id,
                get_text(lang, "settings_updated"),
                reply_markup=settings_menu(chat_id, lang),
            )
        except Exception as e:
            print(f"Error processing setting input: {str(e)}")
            try:
                bot.send_message(chat_id, get_text(lang, "error_occurred"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")

    @bot.message_handler(commands=["random_artist"])
    def send_random_artist(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            username = get_user_setting(chat_id, "username", "")

            if not username:
                bot.reply_to(message, get_text(lang, "set_username_first"))
                return

            temp_message = bot.send_message(
                chat_id, get_text(lang, "generating_random_artist")
            )

            all_artists = get_all_items(username, "", "artists")

            if not all_artists:
                bot.delete_message(chat_id, temp_message.message_id)
                bot.reply_to(message, get_text(lang, "no_artists_found"))
                return

            random_artist = random.choice(all_artists)
            artist_info = format_item_info(random_artist, "artists", username)

            response = f"[{artist_info['name']}](https://stats.fm/artist/{artist_info['stats_id']})\n"
            response += f"{get_text(lang, 'streams').capitalize()}: {artist_info['streams']} - #{artist_info['position']} {get_text(lang, 'mpao')}\n"
            response += f"{get_text(lang, 'first_listen')}: {get_first_last_listen(username, 'artists', artist_info['stats_id'], 'first')}\n"
            response += f"{get_text(lang, 'last_listen')}: {get_first_last_listen(username, 'artists', artist_info['stats_id'], 'last')}\n"

            bot.delete_message(chat_id, temp_message.message_id)

            image = artist_info.get("image")

            if isinstance(image, str) and image != "N/A":
                bot.send_photo(
                    chat_id,
                    image,
                    caption=response,
                    parse_mode="Markdown",
                )
            elif isinstance(image, dict) and image.get("image") != "N/A":
                bot.send_photo(
                    chat_id,
                    image["image"],
                    caption=response,
                    parse_mode="Markdown",
                )
            else:
                bot.send_message(chat_id, response, parse_mode="Markdown")
        except Exception as e:
            print(f"Error sending random artist: {str(e)}")
            try:
                bot.send_message(chat_id, get_text(lang, "error_occurred"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")

    @bot.message_handler(commands=["random_album"])
    def send_random_album(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            username = get_user_setting(chat_id, "username", "")

            if not username:
                bot.reply_to(message, get_text(lang, "set_username_first"))
                return

            temp_message = bot.send_message(
                chat_id, get_text(lang, "generating_random_album")
            )

            all_albums = get_all_items(username, "", "albums")

            if not all_albums:
                bot.delete_message(chat_id, temp_message.message_id)
                bot.reply_to(message, get_text(lang, "no_albums_found"))
                return

            random_album = random.choice(all_albums)
            album_info = format_item_info(random_album, "albums", username)

            response = f"[{album_info['title']}](https://stats.fm/album/{album_info['stats_id']})\n"
            response += f"{get_text(lang, 'artist').capitalize()}:  [{album_info['artist']}](https://stats.fm/artist/{album_info['artist_id']})\n"
            response += f"{get_text(lang, 'streams').capitalize()}: {album_info['streams']} - #{album_info['position']} {get_text(lang, 'mpao')}\n"
            response += f"{get_text(lang, 'first_listen')}: {get_first_last_listen(username, 'albums', album_info['stats_id'], 'first')}\n"
            response += f"{get_text(lang, 'last_listen')}: {get_first_last_listen(username, 'albums', album_info['stats_id'], 'last')}\n"

            bot.delete_message(chat_id, temp_message.message_id)

            if album_info.get("image") and album_info["image"] != "N/A":
                bot.send_photo(
                    chat_id,
                    album_info["image"],
                    caption=response,
                    parse_mode="Markdown",
                )
            else:
                bot.send_message(chat_id, response, parse_mode="Markdown")
        except Exception as e:
            print(f"Error sending random album: {str(e)}")
            try:
                bot.send_message(chat_id, get_text(lang, "error_occurred"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")

    @bot.message_handler(commands=["random"])
    @bot.message_handler(func=lambda message: message.text.lower() == "random")
    def send_random_track(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            username = get_user_setting(chat_id, "username", "")
            auto_preview = get_user_setting(
                chat_id,
                "auto-preview",
                AVAILABLE_SETTINGS["auto-preview"]["default"],
            )

            if not username:
                bot.reply_to(message, get_text(lang, "set_username_first"))
                return

            temp_message = bot.send_message(
                chat_id, get_text(lang, "generating_song")
            )

            all_tracks = get_all_items(username, "", "tracks")

            if not all_tracks:
                bot.delete_message(chat_id, temp_message.message_id)
                bot.reply_to(message, get_text(lang, "no_tracks_found"))
                return

            random_track = random.choice(all_tracks)
            track_info = format_item_info(random_track, "tracks", username)

            response = f"[{track_info['title']}](https://stats.fm/track/{track_info['stats_id']})\n"
            response += f"{get_text(lang, 'artist').capitalize()}: [{track_info['artist']}](https://stats.fm/artist/{track_info['artist_id']})\n"
            response += f"Album: {track_info['album']}\n"
            response += f"{get_text(lang, 'streams').capitalize()}: {track_info['streams']} - #{track_info['position']} {get_text(lang, 'mpao')}\n"
            response += f"{get_text(lang, 'first_listen')}: {get_first_last_listen(username, 'tracks', track_info['stats_id'], 'first')}\n"
            response += f"{get_text(lang, 'last_listen')}: {get_first_last_listen(username, 'tracks', track_info['stats_id'], 'last')}\n"

            bot.delete_message(chat_id, temp_message.message_id)

            markup = None
            if (
                get_user_setting(chat_id, "spotify-connected", False)
                and track_info["spotify_id"] != "N/A"
            ):
                spotify_button = InlineKeyboardButton(
                    get_text(lang, "save_to_spotify"),
                    callback_data=f"save_spotify_{track_info['spotify_id']}",
                )
                markup = InlineKeyboardMarkup().add(spotify_button)

            if track_info["album_image"] != "N/A":
                bot.send_photo(
                    chat_id,
                    track_info["album_image"],
                    caption=response,
                    parse_mode="Markdown",
                    reply_markup=markup,
                )
            else:
                bot.send_message(
                    chat_id,
                    response,
                    parse_mode="Markdown",
                    reply_markup=markup,
                )

            if track_info["preview_url"] != "N/A" and auto_preview:
                try:
                    audio_data = requests.get(
                        track_info["preview_url"], timeout=10
                    ).content
                    bot.send_audio(
                        chat_id,
                        audio_data,
                        title=track_info["title"],
                        performer=track_info["artist"],
                    )
                except requests.exceptions.RequestException:
                    try:
                        bot.reply_to(message, get_text(lang, "unable_to_download"))
                    except Exception as e:
                        print(f"Error sending unable to download message: {str(e)}")
                except Exception as e:
                    print(f"Error sending random track: {str(e)}")
                    try:
                        bot.reply_to(message, get_text(lang, "error_occurred"))
                    except Exception as e:
                        print(f"Error sending error message: {str(e)}")

        except Exception as e:
            print(f"Error sending random track: {str(e)}")
            try:
                bot.send_message(chat_id, get_text(lang, "error_occurred"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")

    @bot.callback_query_handler(
        func=lambda call: call.data.startswith("save_spotify_")
    )
    def save_to_spotify(call):
        try:
            chat_id = call.message.chat.id
            lang = get_user_language(chat_id)
            track_id = call.data.split("_")[2]

            success = add_track_to_playlist(chat_id, track_id)
            if success == "already_saved":
                bot.answer_callback_query(
                    call.id, get_text(lang, "track_already_saved")
                )
            elif success:
                bot.answer_callback_query(
                    call.id, get_text(lang, "track_saved_to_spotify")
                )
            else:
                bot.answer_callback_query(
                    call.id, get_text(lang, "track_save_error")
                )
        except Exception as e:
            print(f"Error in save_to_spotify: {str(e)}")
            try:
                bot.answer_callback_query(
                    call.id, "An error occurred while saving the track."
                )
            except Exception as e:
                print(f"Error answering callback query: {str(e)}")

    @bot.message_handler(commands=["random_genre"])
    def send_random_genre(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            username = get_user_setting(chat_id, "username", "")

            if not username:
                bot.reply_to(message, get_text(lang, "set_username_first"))
                return

            temp_message = bot.send_message(
                chat_id, get_text(lang, "generating_random_genre")
            )

            try:
                all_genres = get_all_items(username, "", "genres")
            except Exception as e:
                print(f"Error in get_all_items: {str(e)}")
                try:
                    bot.delete_message(chat_id, temp_message.message_id)
                    bot.reply_to(message, get_text(lang, "error_occurred"))
                except Exception as e:
                    print(f"Error sending error message: {str(e)}")

            if all_genres:
                random_genre = random.choice(all_genres)
                try:
                    genre_info = format_item_info(random_genre, "genres", username)
                except Exception as e:
                    print(
                        f"Error sending random genre: {str(e)}"
                    )
                    try:
                        bot.delete_message(chat_id, temp_message.message_id)
                        bot.reply_to(message, get_text(lang, "error_occurred"))
                    except Exception as e:
                        print(f"Error sending error message: {str(e)}")
                    return

                response = f"[{genre_info['name'].capitalize()}](https://stats.fm/genre/{genre_info['name']})\n"
                response += (
                    f"{get_text(lang, 'position')}: #{genre_info['position']}\n"
                )
                response += f"{get_text(lang, 'streams').capitalize()}: {genre_info['streams']}\n"

                try:
                    bot.delete_message(chat_id, temp_message.message_id)
                    bot.send_message(chat_id, response, parse_mode="Markdown")
                except Exception as e:
                    print(f"Error sending random genre: {str(e)}")
                    try:
                        bot.reply_to(message, get_text(lang, "error_occurred"))
                    except Exception as e:
                        print(f"Error sending error message: {str(e)}")
            else:
                try:
                    bot.delete_message(chat_id, temp_message.message_id)
                    bot.reply_to(message, get_text(lang, "no_genres_found"))
                except Exception as e:
                    try:
                        bot.reply_to(message, get_text(lang, "error_occurred"))
                    except Exception as e:
                        print(f"Error sending error message: {str(e)}")
        except Exception as e:
            print(f"Error in send_random_genre: {str(e)}")
            try:
                bot.reply_to(message, get_text(lang, "error_occurred"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")

    @bot.message_handler(commands=["language"])
    def set_language(message):
        try:
            chat_id = message.chat.id
            current_lang = get_user_language(chat_id)

            try:
                lang_code = message.text.split()[1].lower()
            except IndexError:
                bot.reply_to(message, get_text(current_lang, "invalid_language"))
                return

            if lang_code in ["en", "it"]:
                try:
                    username = get_user_setting(chat_id, "username", "")
                    set_user_data(chat_id, username, lang_code)
                    bot.reply_to(message, get_text(lang_code, "language_set"))
                except Exception as e:
                    print(f"Error editing language: {str(e)}")
                    bot.reply_to(message, get_text(current_lang, "error_occurred"))
            else:
                bot.reply_to(message, get_text(current_lang, "invalid_language"))
        except Exception as e:
            print(f"General error in set_language: {str(e)}")
            try:
                bot.reply_to(
                    message,
                    get_text(get_user_language(message.chat.id), "error_occurred"),
                )
            except Exception as e:
                print(f"Error sending error message: {str(e)}")

    @bot.message_handler(commands=["help"])
    def send_help(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            bot.reply_to(
                message,
                get_text(lang, "help_message", GITHUB_REPO),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            print(f"Error sending help: {str(e)}")
            try:
                bot.reply_to(message, get_text(lang, "error_occurred"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")

    @bot.message_handler(
        func=lambda message: message.text.lower().startswith(
            ("/top", "/albums", "/artists", "/genres")
        )
    )
    def send_top_items(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            username = get_user_setting(chat_id, "username", "")

            if not username:
                bot.reply_to(message, get_text(lang, "set_username_first"))
                return

            item_type = re.match(r"[a-zA-Z]+", message.text.lstrip("/")).group(0)

            command_parts = message.text.lower().split(item_type)
            if len(command_parts) < 2:
                bot.reply_to(message, get_text(lang, "invalid_top_command"))
                return

            if item_type == "top":
                item_type = "tracks"

            number_period = command_parts[1]
            number = ""
            for char in number_period:
                if char.isdigit():
                    number += char
                else:
                    break

            if not number or int(number) < 1 or int(number) > 250:
                bot.reply_to(message, get_text(lang, "invalid_top_command"))
                return

            period = number_period[len(number) :]
            if period not in ["m", "hy", "lt"]:
                bot.reply_to(message, get_text(lang, "invalid_top_command"))
                return

            period_map = {"m": "weeks", "hy": "months", "lt": "lifetime"}
            api_period = period_map[period]

            temp_message = bot.send_message(
                chat_id, get_text(lang, "generating_top", get_text(lang, item_type))
            )

            all_items = get_all_items(username, api_period, item_type)[
                : int(number)
            ]

            if all_items:
                response = f"Top {number} {get_text(lang, item_type)} {get_text(lang, period)}:\n\n"
                for item in all_items:
                    item_info = format_item_info(item, item_type, username)
                    if item_type == "tracks":
                        response += f"{item_info['position']}. [{item_info['title']}](https://stats.fm/track/{item_info['stats_id']}) - {item_info['artist']} ({item_info['streams']} {get_text(lang, 'streams')})\n"
                    elif item_type == "albums":
                        response += f"{item_info['position']}. [{item_info['title']}](https://stats.fm/album/{item_info['stats_id']}) ({item_info['streams']} {get_text(lang, 'streams')})\n"
                    elif item_type == "artists":
                        response += f"{item_info['position']}. [{item_info['name']}](https://stats.fm/artist/{item_info['stats_id']}) ({item_info['streams']} {get_text(lang, 'streams')})\n"
                    elif item_type == "genres":
                        response += f"{item_info['position']}. [{item_info['name'].capitalize()}](https://stats.fm/genre/{item_info['name']}) ({item_info['streams']} {get_text(lang, 'streams')})\n"

                bot.delete_message(chat_id, temp_message.message_id)
                send_long_message(bot, chat_id, response, parse_mode="Markdown")
            else:
                bot.delete_message(chat_id, temp_message.message_id)
                bot.reply_to(message, get_text(lang, "no_items_found"))
        except Exception as e:
            print(f"Error in send_top_items: {str(e)}")
            if ENV == "debug":
                print(f"Traceback: {traceback.format_exc()}")
            try:
                bot.reply_to(message, get_text(lang, "error_occurred"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")

    @bot.message_handler(commands=["recommend"])
    def send_recommendations(message):
        chat_id = message.chat.id
        lang = get_user_language(chat_id)
        username = get_user_setting(chat_id, "username", "")

        if not username:
            bot.reply_to(message, get_text(lang, "set_username_first"))
            return

        temp_message = bot.send_message(
            chat_id, get_text(lang, "generating_recommendations")
        )

        try:
            recommended_tracks, recommended_albums = get_complex_recommendations(
                username
            )

            response = f"{get_text(lang, 'recommended_tracks')}:\n\n"
            for i, track in enumerate(recommended_tracks, 1):
                try:
                    response += f"{i}.[{track['name']}](https://stats.fm/track/{track['id']}) - [{track['artists'][0]['name']}](https://stats.fm/artist/{track['artists'][0]['id']})\n"
                except (KeyError, IndexError) as e:
                    print(f"Error processing track: {e}")
                    continue

            response += f"\n{get_text(lang, 'recommended_albums')}:\n\n"
            for i, album in enumerate(recommended_albums, 1):
                try:
                    response += f"{i}.[{album['name']}](https://stats.fm/album/{album['id']}) - [{album['artists'][0]['name']}](https://stats.fm/artist/{album['artists'][0]['id']})\n"
                except (KeyError, IndexError) as e:
                    print(f"Error processing album: {e}")
                    continue

            bot.delete_message(chat_id, temp_message.message_id)
            send_long_message(bot, chat_id, response, parse_mode="Markdown")
        except Exception as e:
            print(f"Error getting recommendations: {e}")
            print(f"Traceback: {traceback.format_exc()}")
            try:
                bot.delete_message(chat_id, temp_message.message_id)
                bot.reply_to(message, get_text(lang, "error_recommendations"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")

    @bot.message_handler(commands=["github", "report", "feature", "question"])
    def handle_info_commands(message):
        try:
            chat_id = message.chat.id
            command = message.text.split()[0][1:]  # Extract command without '/'
            lang = get_user_language(chat_id)

            response = get_text(lang, command, GITHUB_REPO)
            bot.reply_to(
                message,
                response,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            print(f"Error handling {command} command: {str(e)}")
            try:
                bot.reply_to(message, get_text(lang, "error_occurred"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")
    
    @bot.message_handler(commands=["albbyyears"])
    def handle_top_albums_by_year(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            temp_message = bot.reply_to(message, get_text(lang, "fetching_top_albums"))
            username = get_user_setting(chat_id, "username", "")
            if not username:
                bot.delete_message(chat_id, temp_message.message_id)
                bot.reply_to(message, get_text(lang, "set_username_first"))
                return
            all_items = get_all_items(username, "lifetime", "albums")

            top_albums_by_year = {}

            for year in range(1971, datetime.now().year+1):
                for album in all_items:
                    release_date = album['album'].get('releaseDate')
                    if release_date:
                        try:
                            release_year = datetime.fromtimestamp(release_date / 1000).year
                            if release_year == year:
                                if year not in top_albums_by_year:
                                    top_albums_by_year[year] = album
                                    break
                        except Exception as e:
                            continue

            # response = get_text(lang, "top_albums_by_year") + ":\n\n"
            response = "\n\n"
            for year, album in top_albums_by_year.items():
                response += (
                    "{}: [{}](https://stats.fm/album/{}) - [{}](https://stats.fm/artist/{}) - {} {}\n".format(
                        year,
                        album['album']['name'].replace('[', '\\[').replace(']', '\\]'),
                        album['album']['id'],
                        album['album']['artists'][0]['name'] if len(album['album']['artists']) > 0 else 'N/A',
                        album['album']['artists'][0]['id'] if len(album['album']['artists']) > 0 else '',
                        album['streams'],
                        get_text(lang, 'streams')
                    )
                )

            bot.delete_message(chat_id, temp_message.message_id)
            send_long_message(bot, chat_id, response, parse_mode="Markdown")
        except Exception as e:
            print(f"Error in albbyyears: {e}")
            bot.delete_message(chat_id, temp_message.message_id)
            bot.reply_to(message, get_text(lang, "set_username_first"))
            return

    @bot.message_handler(commands=["albbyyear"])
    def handle_top_albums(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            args = message.text.split()[1:]
            
            if not args:
                bot.reply_to(message, get_text(lang, "specify_year"))
                return
            
            year = args[0]
            if not year.isdigit() or len(year) != 4:
                bot.reply_to(message, get_text(lang, "invalid_year"))
                return
            
            temp_message = bot.reply_to(message, get_text(lang, "fetching_top_albums"))
            
            username = get_user_setting(chat_id, "username", "")
            if not username:
                bot.delete_message(chat_id, temp_message.message_id)
                bot.reply_to(message, get_text(lang, "set_username_first"))
                return
            
            all_items = get_all_items(username, "lifetime", "albums")

            top_albums = []
            for album in all_items:
                release_date = album['album'].get('releaseDate')
                if release_date:
                    try:
                        release_year = datetime.fromtimestamp(release_date / 1000).year
                        if release_year == int(year):
                            top_albums.append(album)
                    except Exception as e:
                        continue

            if not top_albums:
                bot.delete_message(chat_id, temp_message.message_id)
                bot.reply_to(message, get_text(lang, "no_albums_found"))
                return
            
            response = f"{get_text(lang, 'top_albums_year', year)}:\n\n"
            for i, album in enumerate(top_albums[:10], 1):
                response += f"{i}. [{album['album']['name']}](https://stats.fm/album/{album['album']['id']}) - [{album['album']['artists'][0]['name'] if len(album['album']['artists'])>0 else 'N/A'}](https://stats.fm/artist/{album['album']['artists'][0]['id'] if len(album['album']['artists'])>0 else ''}) - {album['streams']} streams\n"
            
            bot.delete_message(chat_id, temp_message.message_id)
            send_long_message(bot, chat_id, response, parse_mode="Markdown")
        except Exception as e:
            print(f"Error sending top albums: {e}")
            print(f"Traceback: {traceback.format_exc()}")
            try:
                bot.delete_message(chat_id, temp_message.message_id)
                bot.reply_to(message, get_text(lang, "error_top_albums"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")
    
    @bot.message_handler(commands=["now"])
    def handle_now(message):
        try:
            chat_id = message.chat.id
            lang = get_user_language(chat_id)
            username = get_user_setting(chat_id, "username", "")

            if not username:
                bot.reply_to(message, get_text(lang, "set_username_first"))
                return
            temp_message = bot.send_message(
                chat_id, get_text(lang, "generating_now_playing")
            )
            now = get_user_now(username)
            if now:
                response = f"[{now['track']['name']}](https://stats.fm/track/{now['track']['id']})\n"
                response += f"{get_text(lang, 'artist').capitalize()}: [{now['track']['artists'][0]['name']}](https://stats.fm/artist/{now['track']['artists'][0]['id']})\n"
                response += f"Album: [{now['track']['albums'][0]['name']}](https://stats.fm/album/{now['track']['albums'][0]['id']})\n"
                # response += f"{get_text(lang, 'streams').capitalize()}: {now['track']['streams']} - #{now['track']['position']} {get_text(lang, 'mpao')}\n"
                bot.delete_message(chat_id, temp_message.message_id)
                if now['track']['albums'][0]['image'] != "N/A":
                    bot.send_photo(
                        chat_id,
                        now['track']['albums'][0]['image'],
                        caption=response,
                        parse_mode="Markdown",
                    )
                else:
                    bot.send_message(chat_id, response, parse_mode="Markdown")
            else:
                bot.delete_message(chat_id, temp_message.message_id)
                bot.reply_to(message, get_text(lang, "no_now_playing"))
        except Exception as e:
            print(f"Error sending now playing: {str(e)}")
            try:
                bot.send_message(chat_id, get_text(lang, "error_occurred"))
            except Exception as e:
                print(f"Error sending error message: {str(e)}")

    @bot.message_handler(commands=["nowplaying"])
    def handle_nowplaying(message):
        # check if group chat
        if message.chat.type != "group" and message.chat.type != "supergroup":
            bot.reply_to(message, "This command is only available in groups.")
            return
        usernames = get_group_usernames(bot, message.chat.id)
        # foreach user do now = get_user_now(username)
        now = []
        for username in usernames:
            now.append(get_user_now(username))
        # if now is empty, reply with no now playing
        if not any(now):
            bot.reply_to(message, "No one is currently listening to music.")
            return
        # else, reply with a college of all now playing
        



def settings_menu(chat_id, lang):
    try:
        markup = InlineKeyboardMarkup()
        settings = get_user_settings(chat_id)

        for setting_name, setting_info in AVAILABLE_SETTINGS.items():
            try:
                current_value = settings.get(setting_name, setting_info["default"])
                if setting_info["type"] in ["boolean", "callback"]:
                    value_text = "on" if current_value else "off"
                    button_text = (
                        f"{setting_info['label'].get(lang, 'Unknown')}: {value_text}"
                    )
                    markup.row(
                        InlineKeyboardButton(
                            button_text, callback_data=f"toggle_{setting_name}"
                        )
                    )
                elif setting_info["type"] == "text":
                    button_text = f"{setting_info['label'].get(lang, 'Unknown')}: {current_value or 'not set'}"
                    markup.row(
                        InlineKeyboardButton(
                            button_text, callback_data=f"edit_{setting_name}"
                        )
                    )
            except Exception as e:
                print(f"Error processing setting {setting_name}: {str(e)}")
                return InlineKeyboardMarkup()

        return markup
    except Exception as e:
        print(f"Error creating settings menu: {str(e)}")
        return InlineKeyboardMarkup()
