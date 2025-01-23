import sys
import os
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

import telebot
from config import BOT_TOKEN, HOST, PORT, HOST_LOCAL, PORT_LOCAL, ENV
from commands import register_commands
import threading
from callback import app as flask_app

bot = telebot.TeleBot(BOT_TOKEN)


def run_flask():
    try:
        flask_app.run(host=HOST_LOCAL, port=PORT_LOCAL)
    except Exception as e:
        print(f"Flask error: {str(e)}")
        print(traceback.format_exc())


bot_error = None


def run_bot():
    global bot_error
    try:
        register_commands(bot)
        bot.polling(none_stop=True, interval=0, timeout=20)
    except Exception as e:
        bot_error = e
        print(f"Bot error: {str(e)}")
        print(traceback.format_exc())
        bot.stop_polling()


if __name__ == "__main__":
    while True:
        try:
            flask_thread = threading.Thread(target=run_flask)
            bot_thread = threading.Thread(target=run_bot)

            flask_thread.start()
            bot_thread.start()

            flask_thread.join()
            bot_thread.join()

            if bot_error:
                raise bot_error
        except Exception as e:
            print(f"Main loop error: {str(e)}")
            print(traceback.format_exc())
        finally:
            print("Stopping bot and Flask...")
            bot.stop_polling()
            flask_app.shutdown()
            time.sleep(5)
        print("Restarting...")