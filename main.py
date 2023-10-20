import apprise
import configparser
import time
from data_extraction import get_media_from_onleihe, Book, Magazine
from onleihe import Onleihe
from requests.exceptions import RequestException

# Load configuration from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# Get options from config
poll_interval_secs = config.getfloat('GENERAL', 'poll_interval_secs')
auto_rent_keywords_path = config['GENERAL']['auto_rent_keywords_path']

apprise_config_path = config.get('NOTIFICATION', 'apprise_config_path')
test_notification = config.getboolean('NOTIFICATION', 'test_notification')

urls = dict(config['ONLEIHE-URLS'])

username = config['ONLEIHE-CREDENTIALS']['username']
password = config['ONLEIHE-CREDENTIALS']['password']
library = config['ONLEIHE-CREDENTIALS']['library']
library_id = int(config['ONLEIHE-CREDENTIALS']['library-id'])

# Load auto rent filters
with open(auto_rent_keywords_path, 'r') as file:
    auto_rent_keywords = tuple(line.strip() for line in file if not line.strip().startswith('#'))
print(f'{auto_rent_keywords=}')

# Setup Onleihe
onleihe = Onleihe(library=library, library_id=library_id, username=username, password=password)

# Create an Apprise instance
apobj = apprise.Apprise()
config_apprise = apprise.AppriseConfig()
config_apprise.add(apprise_config_path)
apobj.add(config_apprise)


def matches_filter(title, filters):
    title_lower = title.lower()
    for filter_entry in filters:
        if filter_entry.lower() in title_lower:
            return True
    return False


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
        # except Exception as e:
        #    print(f"Unhandled exception: {e}")

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
        auto_rent = False
        new_media = current_media - known_media
        for media in new_media:
            if matches_filter(media.title, auto_rent_keywords):
                print(f'{media.title} matches filter')
                if media.available:
                    print(f'{media.title} is available, trying auto rent')
                    onleihe.rent_media(media)
                    auto_rent = True

            if auto_rent:
                availability_message = 'auto rented :)'
            elif media.available:
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
