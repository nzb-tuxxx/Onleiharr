# Onleiharr

![Telegram Notification](images/onleiharr_telegram.jpg)

## Overview
Onleiharr allows users to monitor specific URLs on the Onleihe website, receive notifications when new media is available, and automatically rent or reserve media based on predefined keywords.

## Features
- **Automatic Login**: Logs in to the Onleihe website using the provided credentials.
- **URL Monitoring**: Continuously checks specified URLs for new media.
- **Notifications**: Sends notifications for new media availability using Apprise.
- **Automatic Renting**: Rent or reserve media based on titles specified in `auto_rent_keywords.txt`.

## Installation and Setup
1. Install the required Python packages:
   ```
   pip install -r requirements.txt
   ```
2. Make a copy of the provided `config.example.ini` template to `config.ini` and `apprise.example.yml` to `apprise.yml`
3. Modify both as per your needs.

## Configuration Details
The `config.ini` file contains several sections:

**[GENERAL]**:
  - `poll_interval_secs`: Interval in seconds between consecutive checks of the Onleihe URLs.
  - `auto_rent_keywords_path`: Path to the text file containing part of the titles of media to be auto-rented.
  
**[NOTIFICATION]**:
  - `apprise_config_path`: Path to the Apprise configuration file for notifications.
  - `test_notification`: Set to `True` to send a test notification on startup. Otherwise, set to `False`.
  - `email`: E-Mail address to receive Onleihe media reservation/availability mails (can be omitted)

**[ONLEIHE-CREDENTIALS]**:
  - `username`: Your Onleihe username.
  - `password`: Your Onleihe password.
  - `library`: The name of your library.
  - `library-id`: Your personal library ID.

**[ONLEIHE-URLS]**: 
  - List of URLs to monitor. Add more URLs as needed. Make sure to sort the page that new media is always at the top.

## Usage
1. Setup `config.ini` and `apprise.yml` to your needs.
2. Specify titles of media you want to auto-rent in `auto_rent_keywords.txt`.
3. Run the `main.py` script.

## License

This project is licensed under the MIT License.

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you'd like to change.

Note: The Onleiharr project and this README are independent and not affiliated with the official Onleihe website or the organizations behind it.
