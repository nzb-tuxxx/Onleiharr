Onleiharr
=========
Onleiharr is a simple daemon designed to periodically monitor the availability of media on the Onleihe website and notify the user of new media additions. This tool can be especially useful for staying updated when new books or magazines are added to your Onleihe library.

Setup
-----
1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Setup Configuration File**:
   Copy the `config.example.ini` file and rename it to `config.ini`. Adjust the configuration to suit your needs.

3. **Apprise Configuration**:
   Onleiharr uses Apprise for sending notifications. You need to set up an Apprise configuration file. An example can be found in `apprise.example.yml`. Adjust this file accordingly and rename it to `apprise.yml`.

Usage
-----
Simply run the `main.py` script:
```bash
python main.py
```
The script will then run in the background, notifying you of new media added to the Onleihe URLs specified in the `config.ini`.

License
-------
This project is licensed under the MIT License.

Contributing
------------
Pull requests are welcome. For major changes, please open an issue first to discuss what you'd like to change.

Contact
-------
For questions or suggestions, feel free to open an issue in this repository.

Note: The Onleiharr project and this README are independent and not affiliated with the official Onleihe website or the organizations behind it.
