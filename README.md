# Camera Auto-Clicking / Photo Share

This repository runs a Telegram bot and a small Flask site for sharing a photo. The web page **does not take a photo automatically**: camera access is optional, the visitor is told that a photo will go to the Telegram account that created the link, and the visitor must enable the camera, take/review a picture, and press **Send photo**.

## Deploy on Render as one Web Service

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather) and keep its token private.
2. Create a Render **Web Service** from this repository.
3. Set the build command to:

   ```sh
   pip install -r requirements.txt
   ```

4. Use this start command (the repository's `Procfile` contains the same command):

   ```sh
   gunicorn --config gunicorn.conf.py server:app
   ```

5. Add these environment variables in Render:

   | Variable | Value |
   | --- | --- |
   | `BOT_TOKEN` | The token from BotFather. Keep it secret. |
   | `WEBSITE_URL` | The service's public HTTPS URL, e.g. `https://my-photo-bot.onrender.com` (no trailing slash). |
   | `BOT_USERNAME` | Optional, without `@`; used only for the home-page button. |
   | `DATABASE_PATH` | Optional. For a mounted Render Disk, use `/var/data/links.sqlite3`. |

   The app can also use Render's `RENDER_EXTERNAL_URL` as a fallback for `WEBSITE_URL`, but setting `WEBSITE_URL` explicitly is recommended.

6. For links to survive restarts and redeploys, attach a persistent Render Disk mounted at `/var/data` and set `DATABASE_PATH=/var/data/links.sqlite3`. Without a persistent disk, the service still runs, but its local database and links can be lost when Render replaces the instance. Existing `links_db.json` data is imported once if that file is present when the SQLite database is first opened.
7. After deploy, open `https://<your-service>.onrender.com/health`. It should return `status: healthy`; `telegram_bot` should become `running`. Then open the bot in Telegram and send `/start`.

### Important Render settings

- Keep the service to **one instance/one Gunicorn worker**. Telegram long polling must not run in multiple web workers or instances with the same bot token. `gunicorn.conf.py` sets one worker for this reason.
- A service that sleeps cannot poll Telegram while it is asleep. For continuous bot availability, use an always-on Render service/worker. If you later split the bot and website into separate services, configure them to share persistent storage or move the database to a shared database.
- Camera access works on HTTPS (Render's public URL is HTTPS). If camera access is denied, visitors can still view the shared photo and nothing is sent.

## Bot flow

1. Send `/start` and choose **📸 Camera Mode** (or `/create`).
2. Send the photo you want to display. The bot creates and returns a unique link immediately.
3. The visitor can view the photo without using a camera. To share a camera photo back, they must accept the on-page disclosure, enable camera access, press **Take photo**, review it, and press **Send photo to link creator**.
4. The bot sends only the visitor-approved photo to the Telegram chat that created that link. The page supports up to two manually sent photos per visit; it does not collect visitor IP addresses or device fingerprints.

Other commands: `/mylinks`, `/help`, and `/cancel`.

## Run locally

```sh
pip install -r requirements.txt
export BOT_TOKEN='your-bot-token'
export WEBSITE_URL='http://localhost:5000'
python server.py
```

Open `http://localhost:5000/health` to check the web process. The bot needs internet access to Telegram. Do not commit `.env`, your bot token, or the SQLite database.

## Run tests

```sh
python -m unittest discover -v
```
