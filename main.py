import apprise
import configparser
import time
from data_extraction import get_media_from_onleihe, Book, Magazine
from requests.exceptions import RequestException

# Load configuration from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# Get options from config
poll_interval_secs = config.getfloat('GENERAL', 'poll_interval_secs')
apprise_config_path = config.get('NOTIFICATION', 'apprise_config_path')
test_notification = config.getboolean('NOTIFICATION', 'test_notification')
urls = dict(config['ONLEIHE'])

# Create an Apprise instance
apobj = apprise.Apprise()
config_apprise = apprise.AppriseConfig()
config_apprise.add(apprise_config_path)
apobj.add(config_apprise)

known_media = set()
first_run = True

while True:
    current_media = set()

    for media_type, url in urls.items():
        try:
            media_from_url = get_media_from_onleihe(url)
            current_media.update(media_from_url)
        except RequestException as e:
            print(f"Network error while processing url {url}: {e}")
        except Exception as e:
            print(f"Unhandled exception: {e}")

    if first_run:
        print("First run, populating cache...")
        for media in current_media:
            print(f"[CACHE] {media}")
        known_media = current_media
        first_run = False

        if test_notification:
            from random import choice
            random_media = choice(list(known_media))
            known_media.remove(random_media)
            print(f"Removed media '{random_media.title}' from known_media for notification test")
    else:
        new_media = current_media - known_media
        for media in new_media:
            if media.available:
                availability_message = 'available'
            else:
                availability_message = f'rented until <b>{media.availability_date}</b>'

            if isinstance(media, Book):
                notify_message = f'[{media.format.upper()}] <a href="{media.full_url}">{media.author} - {media.title}</a> {availability_message}'
            elif isinstance(media, Magazine):
                notify_message = f'[MAGAZINE] <a href="{media.full_url}">{media.title}</a> {availability_message}'

            print(notify_message)
            apobj.notify(
                title='Onleihe: New media',
                body=notify_message,
            )

        known_media.update(new_media)

    time.sleep(poll_interval_secs)